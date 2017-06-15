# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta

__all__ = ['Sale', 'SaleLine']


_STATES_EDIT = ~Eval('state').in_(['draft', 'quotation', 'confirmed', 'processing'])
_STATES_EDIT_LINE = ~Eval('_parent_sale', {}).get('state').in_(['draft'])


class Sale:
    __metaclass__ = PoolMeta
    __name__ = 'sale.sale'

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
                cls._check_modify_exclude_shipment.keys()):
            field = getattr(cls, fname)
            field.states['readonly'] = _STATES_EDIT

        cls._error_messages.update({
                'invalid_edit_method': ('Can not edit sale "%s" '
                    'that invoicing method is not on shipment sent.'),
                'invalid_edit_fields_method': ('Can not edit sale "%s" '
                    'and field %s because sale already invoiced.'),
                'invalid_edit_shipments_method': ('Can not edit sale "%s" '
                    'because sale partially shipped.'),
                'invalid_edit_move': ('Can not edit move "%s" '
                        'that state is not draft.'),

                })

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
                (self.invoice_method != 'shipment')) and
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

        for sales, values in zip(actions, actions):
            for sale in sales:
                if not sale.check_edit_state_method:
                    continue
                if len(sale.shipments) > 1:
                    cls.raise_user_error('invalid_edit_shipments_method',
                        (sale.rec_name,))

                if 'lines' in values:
                    for shipment in sale.shipments:
                        for move in shipment.moves:
                            if move.state != 'draft':
                                cls.raise_user_error('invalid_edit_move',
                                    (move.rec_name,))

                    for v in values['lines']:
                        if 'create' == v[0]:
                            sales_to_process.append(sale)

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


class SaleLine:
    __metaclass__ = PoolMeta
    __name__ = 'sale.line'

    @classmethod
    def __setup__(cls):
        super(SaleLine, cls).__setup__()
        cls._check_modify_exclude = ['quantity', 'unit_price']
        cls._check_readonly_fields = ['type', 'product', 'unit', 'taxes']

        cls._error_messages.update({
                'invalid_edit_move': ('Can not edit move "%s" '
                    'that state is not draft.'),
                'invalid_edit_multimove': ('Can not edit line "%s" '
                    'that has more than one move.'),
                'cannot_edit': ('Can not edit "%s" field.'),
                })

    @property
    def check_line_to_update(self):
        if (self.sale and self.sale.state == 'processing' and self.moves):
            return True
        return False

    @classmethod
    def validate(cls, lines):
        super(SaleLine, cls).validate(lines)

        sales = set(x.sale for x in lines if x.sale)

        # check sale
        for sale in sales:
            if not sale.check_edit_state_method:
                continue
            sale.check_edit_invoice_method()

        # check sale lines
        shipments = set()
        for line in lines:
            if not line.check_line_to_update:
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
    def write(cls, *args):
        pool = Pool()
        ShipmentOut = pool.get('stock.shipment.out')
        Move = Pool().get('stock.move')

        actions = iter(args)
        moves_to_write = []
        shipment_out_waiting = set()
        shipment_out_draft = set()

        for lines, values in zip(actions, actions):
            vals = {}
            check_readonly_fields = []
            for v in values:
                if v in cls._check_readonly_fields:
                    check_readonly_fields.append(v)

            for field in cls._check_modify_exclude:
                if field in values:
                    vals[field] = values.get(field)

            for line in lines:
                if not line.check_line_to_update:
                    continue

                if check_readonly_fields:
                    cls.raise_user_error('cannot_edit',
                        ', '.join(check_readonly_fields))

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

        super(SaleLine, cls).write(*args)

        if moves_to_write:
            Move.write(*moves_to_write)

            # reload inventory_moves from outgoing_moves
            # not necessary in returns and reload inventory_moves from
            # incoming_moves
            if shipment_out_waiting:
                ShipmentOut.draft(list(shipment_out_waiting))
                ShipmentOut.wait(list(shipment_out_waiting))

            if shipment_out_draft:
                ShipmentOut.wait(list(shipment_out_draft))
                ShipmentOut.draft(list(shipment_out_draft))
