# Геометрия, данные и дискретная математика

Все поля на верхнем уровне. Для каждой action разрешены только её аргументы.

## geometry
`shape` и размеры в одной единице длины `unit` (m по умолчанию):
rectangle(length,width), circle(radius), triangle(a,b,c; строгие неравенства треугольника),
box(length,width,height), sphere(radius), cylinder/cone(radius,height),
distance(point1,point2: одинаковые векторы до10), polygon(points: 3–100 двумерных вершин
в порядке обхода, не дублируй первую, без самопересечений),
material(area,piece_area,waste_percent=0: площади в квадрате unit, количество округляется вверх).
Размеры положительные; координаты могут быть отрицательными. Выход содержит площади,
периметры, объёмы или расстояния и их единицы.
Пример: `{"operation":"geometry","shape":"triangle","a":3,"b":4,"c":5,"unit":"cm"}`.

## trigonometry
`angle_unit=rad|deg` (rad). action sin/cos/tan(value), asin/acos/atan(value)
(обратные возвращают угол в angle_unit), degrees(value в радианах), radians(value в градусах),
cosine_side(a,b,angle: угол между сторонами; длины в одной заданной пользователем единице),
triangle_angles(a,b,c: возвращает A,B,C напротив соответствующих сторон).
Пример: `{"operation":"trigonometry","action":"cos","value":60,"angle_unit":"deg"}`.

## statistics
`values`: 1–10000 чисел. Возвращает count/sum/mean/median/min/max/range/variance/stddev.
`sample` boolean (false): делитель n, при true n-1 и n≥2.
`percentile` 0–100: линейная интерполяция позиции (n-1)*p/100.
`weights`: столько же неотрицательных весов, сумма>0 → weighted_mean.
`y`: столько же значений, n≥2 → Pearson correlation и slope/intercept регрессии y=a*x+b;
постоянный ряд отвергается. `unit`: подпись исходных измерений.

## probability
`action`:
factorial(n), combinations(n,k), permutations(n,k=n), binomial(n,k,p): целые0≤k≤n≤1000;
union/intersection/conditional(a,b,intersection): вероятности A,B и пересечения;
либо явно `independent=true`, тогда пересечение a*b. Противоречия отвергаются;
conditional = P(A|B), b>0. expectation(values,probabilities): сумма вероятностей ровно1;
poisson(mean≥0,k0..1000). Вероятности в [0,1].
Пример: `{"operation":"probability","action":"binomial","n":10,"k":2,"p":"1/2"}`.

## unit_convert и dimensions
`unit_convert`: value, from_unit, to_unit, необязательный places. Точные коэффициенты,
KB=1000B, KiB=1024B, b≠B, mW≠MW. Длина mm/cm/m/km/in/ft/yd/mi;
площадь mm2/cm2/m2/km2/ha/ar/ft2; объём ml/l/m3/cm3/gal(US);
масса mg/g/kg/t/lb/oz; время ms/s/min/h/day/week; данные B/bit, KB..PB, KiB..PiB;
скорость B/s, Mbit/s и другие K/M/G-префиксы, m/s/km/h/mph;
мощность W/mW/kW/MW/hp(метрическая), энергия J/kJ/MJ/Wh/kWh/cal/kcal;
частота Hz..GHz, давление Pa/kPa/MPa/bar/atm/psi;
ток A/mA, напряжение V/mV/kV, заряд Ah/mAh; температура C/F/K (ниже0K запрещена).
Поддерживаются распространённые русские обозначения.

`dimensions`: expression, variables:{имя:единица}, expected_unit. Проверяет размерности
**до сокращения**, возвращает compatible. Сложение требует одинаковых размерностей;
умножение/деление, целые степени -20..20, sqrt, abs; sin/cos/tan/exp/ln/log только
безразмерного аргумента. Температуры в формулах только K. Проверка размерностей не
подставляет коэффициенты: сначала приведи значения к общим единицам unit_convert.
Пример: `{"operation":"dimensions","expression":"d/t","variables":{"d":"m","t":"s"},"expected_unit":"m/s"}`.

## bits
action base: value строка до1024 символов, base 2–36 (10), target_base2–36 (16).
action and/or/xor: value,other; not:value; left_shift/right_shift:value,shift0..width.
Для битовых операций width1–1024 (32), операнды беззнаковые в пределах маски;
выход за ширину при сдвиге отбрасывается. Выход: decimal, binary, hex.

## discrete
Множества строк: action union/intersection/difference/symmetric_difference/subset,
left/right списки до10000 строк. Логика: and/or/xor/implies(a,b), not(a), строго boolean.
Граф: action shortest_path или topological, nodes 1–200 уникальных строк,
edges до2000 [from,to,weight=1]. Для маршрута source/target и directed boolean (true),
веса≥0. Для порядка зависимостей рёбра от предшественника к зависимому, циклы — ошибка.
Пример: `{"operation":"discrete","action":"shortest_path","nodes":["A","B"],"edges":[["A","B",3]],"source":"A","target":"B"}`.
