# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pyson import Eval, If
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

        # TODO update current readonly
        cls.description.states['readonly'] = _STATES_EDIT
        cls.sale_date.states['readonly'] = _STATES_EDIT
        cls.payment_term.states['readonly'] = _STATES_EDIT
        # # party
        # # invoice_address
        # # shipment_party
        # # shipment_address
        # # warehouse
        # # currency
        cls.lines.size = If(Eval('state') == 'processing', 1, 9999999)
        cls.lines.states['readonly'] = _STATES_EDIT
        cls._error_messages.update({
                'invalid_edit_method': ('Can not edit sale "%s" '
                    'that invoicing method is not on shipment sent.'),
                })

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
        if self.check_edit_state_method() and (self.invoice_method != 'shipment'):
            self.raise_user_error('invalid_edit_method', (self.rec_name,))


class SaleLine:
    __metaclass__ = PoolMeta
    __name__ = 'sale.line'

    @classmethod
    def __setup__(cls):
        super(SaleLine, cls).__setup__()

        cls.type.states['readonly'] = _STATES_EDIT_LINE
        cls.product.states['readonly'] |= _STATES_EDIT_LINE
        cls.unit.states['readonly'] = _STATES_EDIT_LINE
        cls.taxes.states['readonly'] = _STATES_EDIT_LINE
        cls._error_messages.update({
                'invalid_edit_move': ('Can not edit move "%s" '
                    'that state is assigned, done or cancel.'),
                'invalid_edit_multimove': ('Can not edit line "%s" '
                    'that has more than one move.'),
                })

    @classmethod
    def validate(cls, lines):
        super(SaleLine, cls).validate(lines)

        sales = set()
        for line in lines:
            if line.sale:
                sales.add(line.sale)

        # check sale
        for sale in sales:
            sale.check_edit_invoice_method()

        # check sale lines
        for line in lines:
            if not line.sale or not line.sale.state == 'processing':
                continue

            moves = line.moves
            if len(moves) > 1:
                cls.raise_user_error('invalid_edit_multimove', (line.rec_name))

            for move in moves:
                # TOOD outgoing move state is draft when shipment is assigned
                if move.state in ['assigned', 'done', 'cancel']:
                    cls.raise_user_error('invalid_edit_move', (move.rec_name))

    @classmethod
    def write(cls, *args):
        pool = Pool()
        ShipmentOut = pool.get('stock.shipment.out')
        Move = Pool().get('stock.move')

        actions = iter(args)
        moves_to_write = []
        shipment_out_waiting = set()
        for lines, values in zip(actions, actions):
            vals = {}
            if 'quantity' in values:
                vals['quantity'] = values['quantity']
            if 'unit_price' in values:
                vals['unit_price'] = values['unit_price']

            if vals:
                for line in lines:
                    if not line.sale or not line.sale.state == 'processing' \
                            or not line.moves:
                        continue
                    # get first move because in validate we check that can not edit
                    # a line that has more than one move
                    move, = line.moves
                    moves_to_write.extend(([move], vals))

                    if move.shipment:
                        if move.shipment.__name__ == 'stock.shipment.out':
                            shipment = move.shipment
                            if shipment.state == 'waiting':
                                shipment_out_waiting.add(shipment)

        super(SaleLine, cls).write(*args)

        if moves_to_write:
            Move.write(*moves_to_write)

            # reload inventory_moves from outgoing_moves
            # not necessary in returns and reload inventory_moves from incoming_moves
            if shipment_out_waiting:
                ShipmentOut.draft(list(shipment_out_waiting))
                ShipmentOut.wait(list(shipment_out_waiting))
