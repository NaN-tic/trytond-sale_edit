# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.transaction import Transaction

__all__ = ['ShipmentOut']


class ShipmentOut(metaclass=PoolMeta):
    __name__ = 'stock.shipment.out'

    @classmethod
    def write(cls, *args):
        super(ShipmentOut, cls).write(*args)
        actions = iter(args)
        args = []

        for shipments, values in zip(actions, actions):
            if not shipments:
                args.extend((shipments, values))
            for shipment in shipments:
                if shipment.state not in ('done', 'cancelled'):
                    update_amounts = Transaction().context.get(
                        'update_amounts', False)
                    if hasattr(cls, 'calc_amounts') and update_amounts:
                        values.update(shipment.calc_amounts())
                args.extend(([shipment], values))
        super(ShipmentOut, cls).write(*args)
