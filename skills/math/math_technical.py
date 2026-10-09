"""Deterministic engineering/IT estimates from explicit user inputs."""
import ipaddress

import sympy as sp

from math_common import fields, integer, number, outcome
from math_units import _lookup


def quantity(value, unit, target):
    dimension, factor = _lookup(unit)
    target_dimension, target_factor = _lookup(target)
    if dimension != target_dimension:
        raise ValueError(f"несовместимые единицы: {unit} -> {target}")
    return number(value, minimum=0)*sp.Rational(str(factor))/sp.Rational(str(target_factor))


def technical(p, places=None):
    allowed = {"cidr": "cidr", "subnets": "cidr prefix", "raid": "disks disk_size disk_unit raid reserve_percent",
               "growth": "initial growth_percent periods size_unit", "backup": "size size_unit copies retention_days daily_change_percent",
               "transfer": "size size_unit speed speed_unit efficiency", "power": "voltage current power_factor phases",
               "current": "power power_unit voltage", "voltage": "power power_unit current", "ohm": "voltage resistance",
               "energy": "power power_unit hours headroom_percent", "surveillance": "cameras bitrate bitrate_unit days duty_cycle"}
    fields(p, "action " + allowed.get(p.get("action"), ""), "action")
    action = p["action"]
    def n(key, default=None, positive=False):
        return number(p.get(key, default), key, minimum=0, positive=positive)
    def fraction(key, default=1):
        value = n(key, default)
        if value > 1:
            raise ValueError(f"{key}: доля от 0 до 1")
        return value
    if action in {"cidr", "subnets"}:
        network = ipaddress.ip_network(p.get("cidr", ""), strict=True)
        first = network.network_address
        last = network.broadcast_address
        excluded = 2 if network.version == 4 and network.prefixlen < 31 else 1 if network.version == 6 and network.prefixlen < 127 else 0
        result = {"network": str(network), "mask": str(network.netmask), "first": str(first), "last": str(last),
                  "addresses": network.num_addresses, "usable_addresses": network.num_addresses-excluded,
                  "first_usable": str(first+1) if excluded else str(first),
                  "last_usable": str(last-1) if excluded == 2 else str(last),
                  "broadcast": str(last) if network.version == 4 else None}
        if action == "subnets":
            prefix = integer(p.get("prefix"), "prefix", network.prefixlen, network.max_prefixlen)
            if 2**(prefix-network.prefixlen) > 1024:
                raise ValueError("не более 1024 подсетей за расчёт")
            result["subnets"] = [str(item) for item in network.subnets(new_prefix=prefix)]
        return outcome(result, "addresses=2^(address_bits-prefix)", p)
    if action == "raid":
        disks = integer(p.get("disks"), "disks", 1, 10000)
        size = quantity(p.get("disk_size"), p.get("disk_unit", "TB"), "B")
        raid = str(p.get("raid", ""))
        reserve = n("reserve_percent", 0)
        if reserve >= 100 or size <= 0:
            raise ValueError("reserve_percent <100; disk_size >0")
        minimum = {"0": 1, "1": 2, "5": 3, "6": 4, "10": 4}
        if raid not in minimum or disks < minimum[raid] or raid == "10" and disks%2:
            raise ValueError("RAID 0/1/5/6/10: неверное число дисков")
        factor = disks if raid == "0" else 1 if raid == "1" else disks-1 if raid == "5" else disks-2 if raid == "6" else disks//2
        result = {"raw_bytes": disks*size, "usable_bytes": factor*size, "budget_bytes": factor*size*(1-reserve/100),
                  "assumption": "одинаковые диски; без filesystem/controller overhead; RAID1 mirrors all disks"}
        formula, units = "usable=effective_disks*disk_size; budget=usable*(1-reserve/100)", {"raw_bytes": "B", "usable_bytes": "B", "budget_bytes": "B"}
    elif action == "growth":
        initial = quantity(p.get("initial"), p.get("size_unit", "GB"), "B")
        rate = n("growth_percent")/100
        periods = integer(p.get("periods"), "periods", 0, 1000)
        result, formula, units = {"bytes": initial*(1+rate)**periods}, "final=initial*(1+growth/100)^periods", {"bytes": "B"}
    elif action == "backup":
        size = quantity(p.get("size"), p.get("size_unit", "GB"), "B")
        copies = integer(p.get("copies", 1), "copies", 1, 1000)
        days = integer(p.get("retention_days"), "retention_days", 1, 3660)
        change = n("daily_change_percent")/100
        if change > 1:
            raise ValueError("daily_change_percent: 0–100")
        result, formula, units = {"bytes": size*(1+(days-1)*change)*copies, "assumption": "one full + retained daily incrementals; no compression/dedup"}, "size*(1+(days-1)*change/100)*copies", {"bytes": "B"}
    elif action == "transfer":
        size = quantity(p.get("size"), p.get("size_unit", "GB"), "B")
        speed = quantity(p.get("speed"), p.get("speed_unit", "Mbit/s"), "B/s")
        efficiency = fraction("efficiency")
        if speed <= 0 or efficiency <= 0:
            raise ValueError("скорость и efficiency должны быть >0")
        result, formula, units = {"seconds": size/(speed*efficiency)}, "time=size/(speed*efficiency)", {"seconds": "s"}
    elif action == "power":
        # Voltage/current are SI V/A; phase topology and power factor are explicit.
        phases = integer(p.get("phases", 1), "phases", 1, 3)
        if phases not in (1, 3):
            raise ValueError("phases: 1 или 3")
        pf = fraction("power_factor")
        watts = n("voltage", positive=True)*n("current")*pf*(sp.sqrt(3) if phases == 3 else 1)
        result, formula, units = {"watts": watts}, "P=U*I*power_factor*(sqrt(3) for three-phase line voltage)", {"watts": "W"}
    elif action in {"current", "voltage", "ohm"}:
        if action == "current":
            value, formula, key, unit = quantity(p.get("power"), p.get("power_unit", "W"), "W")/n("voltage", positive=True), "I=P/U (DC or unity power factor)", "current", "A"
        elif action == "voltage":
            value, formula, key, unit = quantity(p.get("power"), p.get("power_unit", "W"), "W")/n("current", positive=True), "U=P/I (DC or unity power factor)", "voltage", "V"
        else:
            value, formula, key, unit = n("voltage")/n("resistance", positive=True), "I=U/R", "current", "A"
        result, units = {key: value}, {key: unit}
    elif action == "energy":
        watts = quantity(p.get("power"), p.get("power_unit", "W"), "W")
        hours, reserve = n("hours"), n("headroom_percent", 0)
        result, formula, units = {"kwh": watts*hours/1000, "required_watts": watts*(1+reserve/100)}, "E=P*t/1000; capacity=P*(1+headroom/100)", {"kwh": "kWh", "required_watts": "W"}
    elif action == "surveillance":
        cameras = integer(p.get("cameras"), "cameras", 1, 100000)
        bitrate = quantity(p.get("bitrate"), p.get("bitrate_unit", "Mbit/s"), "B/s")
        result, formula, units = {"bytes": cameras*bitrate*n("days")*86400*fraction("duty_cycle")}, "bytes=cameras*bytes_per_second*days*86400*duty_cycle", {"bytes": "B"}
    else:
        raise ValueError("неизвестный технический расчёт")
    return outcome(result, formula, p, units=units, places=places)


def finance_extended(operation, p, places=None):
    allowed = {"compound_interest": "principal rate_percent periods contribution", "depreciation": "cost salvage life period method rate_percent", "break_even": "fixed_cost unit_price variable_cost"}
    fields(p, "currency " + allowed[operation])
    def n(key, default=None, positive=False):
        return number(p.get(key, default), key, minimum=0, positive=positive)
    if operation == "compound_interest":
        principal, rate = n("principal"), number(p.get("rate_percent"), "rate_percent")/100
        periods = integer(p.get("periods"), "periods", 0, 1000)
        payment = n("contribution", 0)
        if rate <= -1:
            raise ValueError("rate_percent > -100")
        factor = (1+rate)**periods
        total = principal*factor+payment*((factor-1)/rate if rate else periods)
        values, formula = {"total": total, "contributed": principal+payment*periods, "interest": total-principal-payment*periods}, "FV=P*(1+r)^n+C*((1+r)^n-1)/r; contributions at period end; r is per-period rate"
    elif operation == "depreciation":
        cost, salvage = n("cost"), n("salvage", 0)
        life = integer(p.get("life"), "life", 1, 1000)
        period = integer(p.get("period"), "period", 0, life)
        if salvage > cost:
            raise ValueError("salvage не может превышать cost")
        method = p.get("method", "straight_line")
        if method == "straight_line":
            expense = (cost-salvage)/life
            book = cost-expense*period
            accumulated = expense*period
        elif method == "declining_balance":
            rate = n("rate_percent")/100
            if rate > 1:
                raise ValueError("rate_percent: 0–100")
            book = max(salvage, cost*(1-rate)**period)
            previous = max(salvage, cost*(1-rate)**max(0, period-1))
            expense, accumulated = previous-book, cost-book
        else:
            raise ValueError("method: straight_line или declining_balance")
        values, formula = {"book_value": book, "period_expense": expense if period else sp.Integer(0), "accumulated": accumulated}, "straight=(cost-salvage)/life; declining=max(salvage,cost*(1-rate)^period)"
    elif operation == "break_even":
        fixed, price, variable = n("fixed_cost"), n("unit_price"), n("variable_cost")
        if price <= variable:
            raise ValueError("нет конечной точки безубыточности: цена <= переменных затрат")
        count = fixed/(price-variable)
        values, formula = {"units_exact": count, "whole_units": sp.ceiling(count), "revenue": count*price}, "units=fixed_cost/(unit_price-variable_cost)"
    else:
        raise ValueError("неизвестная финансовая операция")
    return outcome(values, formula, p, units={"money": p.get("currency", "currency as supplied")}, places=2 if places is None else places)
