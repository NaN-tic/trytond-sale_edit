# This file is part sale_edit module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import sale
from . import shipment

def register():
    Pool.register(
        sale.Sale,
        sale.SaleLine,
        shipment.ShipmentOut,
        module='sale_edit', type_='model')
