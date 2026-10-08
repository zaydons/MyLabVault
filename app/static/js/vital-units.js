/**
 * Vital units in the browser: convert between a type's units and format readings in the
 * preferred unit (Settings → General → Units). Mirrors convert/format_reading in
 * api/routers/vitals.py. `types` is the response of GET /api/vitals/types.
 */
(function () {
    function unitInfo(types, type, unit) {
        const units = types[type].units;
        return units[unit] || units[Object.keys(units)[0]];
    }

    // value in from_unit -> base unit -> to_unit
    function convert(types, type, value, fromUnit, toUnit) {
        if (value === null || value === undefined) return null;
        const src = unitInfo(types, type, fromUnit);
        const dst = unitInfo(types, type, toUnit);
        return (value * src.factor + src.offset - dst.offset) / dst.factor;
    }

    function round(types, type, unit, value) {
        const decimals = unitInfo(types, type, unit).decimals ?? 1;
        const f = Math.pow(10, decimals);
        return Math.round(value * f) / f;
    }

    function preferred(types, type) {
        return types[type].preferred_unit || Object.keys(types[type].units)[0];
    }

    // "154.3 lb", "120/80 mmHg", "5′ 10″" (height in inches shows feet and inches)
    function format(types, type, value, value2, unit, toUnit) {
        toUnit = toUnit || preferred(types, type);
        const shown = convert(types, type, value, unit, toUnit);
        if (type === 'height' && toUnit === 'in') {
            const total = Math.round(shown);
            return `${Math.floor(total / 12)}′ ${total % 12}″`;
        }
        if (value2 !== null && value2 !== undefined) {
            return `${round(types, type, toUnit, shown)}/${round(types, type, toUnit, convert(types, type, value2, unit, toUnit))} ${toUnit}`;
        }
        return `${round(types, type, toUnit, shown)} ${toUnit}`;
    }

    window.VitalUnits = { convert, round, preferred, format };
})();
