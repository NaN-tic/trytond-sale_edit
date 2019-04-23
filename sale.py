# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta
from trytond.model import fields
from trytond.transaction import Transaction

__all__ = ['Sale', 'SaleLine']


_STATES_EDIT = ~Eval('state').in_(['draft', 'quotation', 'confirmed',
        'processing'])
_STATES_EDIT_LINE = ~Eval('_parent_sale', {}).get('state').in_(['draft'])


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.sale'
    shipment_moves = fields.Function(fields.Many2Many('stock.move', None, None,
        'Moves'), 'get_shipment_moves')

    @classmethod
    def __setup__(cls):
        super(Sale, cls).__setup__()
        cls._check_modify_exclude = ['description', 'payment_term',
            'invoice_address',  'lines']

        if hasattr(cls, 'payment_type'):
                cls._check_modify_exclude += ['payment_type']

        cls._check_modify_exclude_shipment = {
            'shipment_address': 'delivery_address',
            'shipment_party': 'customer',
        }

        for fname in (cls._check_modify_exclude +
                list(cls._check_modify_exclude_shipment.keys())):
            field = getattr(cls, fname)
            field.states['readonly'] = _STATES_EDIT
            if not hasattr(field, 'depends'):
                field.depends = ['state']
            elif not 'state' in field.depends:
                field.depends.append('state')

        # also, lines can't edit when shipment state was sent
        cls.lines.states['readonly'] = (_STATES_EDIT |
            Eval('shipment_state').in_( ['sent', 'exception']))
        cls.lines.depends.append('shipment_state')

        cls._error_messages.update({
                'invalid_edit_method': ('Can not edit sale "%s" '
                    'that invoicing method is not on shipment sent.'),
                'invalid_edit_fields_method': ('Can not edit sale "%s" '
                    'and field %s because sale already invoiced.'),
                'invalid_edit_shipments_method': ('Can not edit sale "%s" '
                    'because sale partially shipped.'),
                'invalid_edit_move': ('Can not edit move "%s" '
                        'that state is not draft.'),
                'invalid_delete_line': ('Can not delete a line "%s"'),
                })

    def get_shipment_moves(self, name):
        '''
        Get all moves from a sale; outgoing_moves, incoming_moves and
        inventory_moves
        '''
        moves = []
        for shipment in self.shipments:
            for move in shipment.moves:
                moves.append(move.id)
        for shipment in self.shipment_returns:
            for move in shipment.moves:
                moves.append(move.id)
        return moves

    @property
    def check_edit_state_method(self):
        '''
        Check edit state method.
        '''
        if self.state == 'processing':
            return True
        return False

    def check_edit_invoice_method(self):
        '''
        Check edit invoice method.
        '''
        if ((self.check_edit_state_method and
                (self.invoice_method not in ('shipment', 'manual'))) and
                len(self.shipments) > 1):
            self.raise_user_error('invalid_edit_method', (self.rec_name,))

    @classmethod
    def validate(cls, sales):
        super(Sale, cls).validate(sales)

        # check sale
        for sale in sales:
            if not sale.check_edit_state_method:
                continue
            sale.check_edit_invoice_method()

    @classmethod
    def write(cls, *args):
        pool = Pool()
        ShipmentOut = pool.get('stock.shipment.out')

        actions = iter(args)
        shipment_to_write = []
        sales_to_process = []
        cache_to_update = []

        for sales, values in zip(actions, actions):
            for sale in sales:
                if not sale.check_edit_state_method:
                    continue

                if 'lines' in values:
                    if len(sale.shipments) > 1:
                        cls.raise_user_error('invalid_edit_shipments_method',
                            (sale.rec_name,))
                    if len(sale.shipment_returns) > 1:
                        cls.raise_user_error('invalid_edit_shipments_method',
                            (sale.rec_name,))

                    cache_to_update.append(sale)

                    for move in sale.shipment_moves:
                        if move.state != 'draft':
                            cls.raise_user_error('invalid_edit_move',
                                (move.rec_name,))

                    for v in values['lines']:
                        if 'create' == v[0]:
                            sales_to_process.append(sale)
                        if 'delete' == v[0]:
                            cls.raise_user_error('invalid_delete_line',
                                (sale.rec_name,))

                for v in values:
                    if v in cls._check_modify_exclude and sale.invoices:
                        cls.raise_user_error('invalid_edit_fields_method',
                            (sale.rec_name, v))

                vals = {}
                for field in cls._check_modify_exclude_shipment:
                    m = cls._check_modify_exclude_shipment
                    if values.get(field):
                        vals[m[field]] = values.get(field)

                if vals:
                    for shipment in sale.shipments:
                        shipment_to_write.extend(([shipment], vals))

        super(Sale, cls).write(*args)

        if shipment_to_write:
            ShipmentOut.write(*shipment_to_write)

        if sales_to_process:
            cls.process(sales_to_process)

        if cache_to_update:
            for sale in cache_to_update:
                sale.untaxed_amount_cache = None
                sale.tax_amount_cache = None
                sale.total_amount_cache = None
            cls.save(cache_to_update)
            cls.store_cache(cache_to_update)

    def _get_shipment_sale(self, Shipment, key):
        # return sale shipment to continue picking or create new shipment
        if Shipment.__name__ == 'stock.shipment.out':
            shipments = self.shipments
            if shipments and len(shipments) == 1:
                shipment, = shipments
                drafts = True
                for move in shipment.moves:
                    if move.state != 'draft':
                        drafts = False
                if drafts:
                    return shipment
        elif Shipment.__name__ == 'stock.shipment.out.return':
            shipment_returns = self.shipment_returns
            if shipment_returns and len(shipment_returns) == 1:
                shipment, = shipment_returns
                drafts = True
                for move in shipment.moves:
                    if move.state != 'draft':
                        drafts = False
                if drafts:
                    return shipment
        return super(Sale, self)._get_shipment_sale(Shipment, key)

    @classmethod
    def cache_to_update(cls, sales):
        for sale in sales:
            sale.untaxed_amount_cache = None
            sale.tax_amount_cache = None
            sale.total_amount_cache = None
        cls.save(sales)
        cls.store_cache(sales)


class SaleLine(metaclass=PoolMeta):
    __name__ = 'sale.line'

    @classmethod
    def __setup__(cls):
        super(SaleLine, cls).__setup__()
        cls._check_modify_exclude = ['product', 'unit', 'quantity',
            'unit_price']
        cls._check_readonly_fields = []
        cls._line2move = {'unit': 'uom'}

        cls._error_messages.update({
                'invalid_edit_move': ('Can not edit move "%s" '
                    'that state is not draft.'),
                'invalid_edit_multimove': ('Can not edit line "%s" '
                    'that has more than one move.'),
                'cannot_edit': ('Can not edit "%s" field.'),
                })

    def check_line_to_update(self, fields):
        if (self.sale and self.sale.state == 'processing' and self.moves):
            # No check should be held if the only fields updated are
            # 'moves_recreated' or 'moves_ignored'
            if set(fields) - {'moves_recreated', 'moves_ignored'}:
                return True
            else:
                return False
        if (self.sale and self.sale.state in ['confirmed', 'processing']):
            return True
        return False

    @classmethod
    def check_editable(cls, lines, fields):
        sales = set(x.sale for x in lines if x.sale)

        # check sale
        for sale in sales:
            if not sale.check_edit_state_method:
                continue
            sale.check_edit_invoice_method()

        # check sale lines
        shipments = set()
        for line in lines:
            if not line.check_line_to_update(fields):
                continue

            moves = line.moves
            if len(moves) > 1:
                cls.raise_user_error('invalid_edit_multimove', (line.rec_name))
            for move in moves:
                if move.shipment:
                    shipments.add(move.shipment)

        for shipment in shipments:
            for move in shipment.moves:
                if move.state != 'draft':
                    cls.raise_user_error('invalid_edit_move', (move.rec_name,))

    @classmethod
    def create(cls, vlist):
        Sale = Pool().get('sale.sale')

        lines = super(SaleLine, cls).create(vlist)

        sale_ids = []
        for values in vlist:
            if values.get('sale'):
                sale_ids.append(values['sale'])

        if sale_ids:
            cache_to_update = Sale.search([
                ('id', 'in', sale_ids),
                ('state', 'in', ['confirmed', 'processing']),
                ])
            if cache_to_update:
                Sale.cache_to_update(cache_to_update)

        return lines

    @classmethod
    def write(cls, *args):
        pool = Pool()
        Sale = pool.get('sale.sale')
        ShipmentOut = pool.get('stock.shipment.out')
        Move = pool.get('stock.move')

        actions = iter(args)
        moves_to_write = []
        shipment_out_waiting = set()
        shipment_out_draft = set()
        cache_to_update = set()

        for lines, values in zip(actions, actions):
            vals = {}
            check_readonly_fields = []
            for v in values:
                if v in cls._check_readonly_fields:
                    check_readonly_fields.append(v)

            for field in cls._check_modify_exclude:
                if field in values:
                    val = values.get(field)
                    mfield = cls._line2move.get(field)
                    if mfield:
                        vals[mfield] = val
                    else:
                        if field == 'quantity':
                            val = abs(val)
                        vals[field] = val

            for line in lines:
                if not line.check_line_to_update(values.keys()):
                    continue

                if ('quantity' in values or 'unit_price' in values
                        or 'discount' in values):
                    if (line.sale.untaxed_amount_cache
                            or line.sale.tax_amount_cache
                            or line.sale.total_amount_cache):
                        cache_to_update.add(line.sale)

                # check that not change type line
                if 'type' in values and values['type'] != line.type:
                    cls.raise_user_error('cannot_edit', 'type')
                if check_readonly_fields:
                    cls.raise_user_error('cannot_edit',
                        ', '.join(check_readonly_fields))

                if len(line.moves) == 1:
                    # get first move because in validate we check that can not
                    # edit a line that has more than one move
                    move, = line.moves
                    moves_to_write.extend(([move], vals))

                    if move.shipment:
                        if move.shipment.__name__ == 'stock.shipment.out':
                            shipment = move.shipment
                            if shipment.state == 'waiting':
                                shipment_out_waiting.add(shipment)
                            if shipment.state == 'draft':
                                shipment_out_draft.add(shipment)

            cls.check_editable(lines, values.keys())

        super(SaleLine, cls).write(*args)

        if cache_to_update:
            Sale.cache_to_update(list(cache_to_update))

        if moves_to_write:
            Move.write(*moves_to_write)

            # reload inventory_moves from outgoing_moves
            # not necessary in returns and reload inventory_moves from
            # incoming_moves
            if shipment_out_draft:
                ShipmentOut.wait(list(shipment_out_draft))

            with Transaction().set_context(update_amounts=True):
                if shipment_out_waiting or shipment_out_draft:
                    ShipmentOut.draft(list(shipment_out_waiting) +
                        list(shipment_out_draft))

                if shipment_out_waiting:
                    ShipmentOut.wait(list(shipment_out_waiting))

    @classmethod
    def delete(cls, lines):
        Sale = Pool().get('sale.sale')

        cache_to_update = set()
        for line in lines:
            if line.sale and (line.sale.untaxed_amount_cache
                    or line.sale.tax_amount_cache
                    or line.sale.total_amount_cache):
                cache_to_update.add(line.sale)

        super(SaleLine, cls).delete(lines)

        if cache_to_update:
            Sale.cache_to_update(list(cache_to_update))
