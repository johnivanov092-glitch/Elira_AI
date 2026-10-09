# Алгебра и анализ

Все поля на верхнем уровне JSON. `expression` — безопасное выражение без Python-кода:
числа/дроби, + - * / **, ^, %, sqrt/root, abs, min/max, floor/ceil, round(x[,places]),
exp, ln/log, log2/log10, sin/cos/tan/asin/acos/atan/sinh/cosh/tanh, gcd/lcm, factorial,
binomial. Тригонометрия выражений — радианы. factorial/binomial ограничены 1000.
Строки, импорты, индексы и атрибуты запрещены. Имена переменных: латинские буквы/цифры.
`places`: целое 0–20; без него точная форма плюс десятичная (бесконечная дробь — 20 знаков).

| operation | Поля |
| --- | --- |
| evaluate | expression, например `0.1+0.2`, `125000*16%`, `round(2.675,2)`. Свободных переменных быть не должно. |
| simplify / expand / factor | expression. Символьное преобразование. |
| solve | expression: уравнения через `;`, variable: имена через запятую (или выводятся из выражения). Область комплексная; solution_set может быть бесконечным или условным. |
| inequality | relations: список 1–20 строк, каждое с одним < <= > >= == !=; variable (x). Одна действительная переменная. |
| nsolve | expression, variable (x), bracket:[lo,hi], tolerance 1e-40..1e-3 (1e-20). Численный поиск бисекцией с проверкой невязки; один корень, не все корни. |
| diff | expression, variable (определяется), order 1–10 (1). |
| integrate | expression, variable; lower и upper обе строки либо обе отсутствуют. Неопределённый интеграл содержит +C. |
| limit | expression, variable (x), point (0, число или inf/-inf), direction + / - / +- (двусторонний). |
| summation | expression, variable (x), lower целое -10000..10000 (0), upper целое ≥lower до10000 или inf (по умолчанию). |
| series | expression, variable (x), point (0), order 1–30 (6): ряд с остатком O. |
| sequence | expression, variable (x), values: 1–1000 значений аргумента. |

Примеры: `{"operation":"solve","expression":"2*x+y=10;x-y=2"}`;
`{"operation":"limit","expression":"sin(x)/x","point":0}`;
`{"operation":"nsolve","expression":"x**2-2","bracket":[1,2],"places":6}`.

## Линейная алгебра

`operation=linear_algebra`, action:
- determinant, rank, inverse, transpose: matrix, до20×20, числовые строки одинаковой длины.
- add, multiply: matrix и other (матрица), совместимые размерности.
- solve: matrix и rhs (вектор), возвращает consistent/unique и решения, включая свободные параметры.
- dot, cross: vector и other (векторы), cross только3D.
- norm: vector. До100 компонент.

Пример: `{"operation":"linear_algebra","action":"determinant","matrix":[[1,2],[3,4]]}`.

## Оптимизация

`operation=optimize, action=linear`: objective — коэффициенты до20 переменных,
`goal=min|max` (min); необязательные matrix/rhs для A*x≤b, equal_matrix/equal_rhs для равенств.
`bounds`: список [lower,upper] на каждую переменную, null — без границы;
без bounds все x≥0. Ограничения до20 строк каждый набор. Точный симплекс, ответ проверяется
обратной подстановкой. Несовместимость и неограниченность — ошибка, не найденный оптимум.

`action=polynomial`: expression, variable (x), lower < upper. Полином степени≤10
на замкнутом интервале; проверяются все стационарные точки и оба конца.
Общее нелинейное программирование сюда не входит.

Пример: `{"operation":"optimize","action":"linear","goal":"max","objective":[3,2],"matrix":[[1,1]],"rhs":[4]}`.
