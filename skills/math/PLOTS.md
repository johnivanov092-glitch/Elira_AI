# Графики

`operation=plot`, path — абсолютный путь нового PNG в существующей рабочей папке.
Существующие файлы не заменяются. Нужен matplotlib из requirements.txt внутри .venv.
Общие подписи: title, x_label, y_label (укажи единицы).

- kind=function: expression, variable(x), lower<upper, samples2..2000 (200).
  Все другие параметры формулы предварительно подставь в expression.
- kind=normal: mean(0),stddev>0,lower<upper,samples2..2000 (200).
- kind=binomial: n1..500,p0..1.
- kind=data: x,y — одинаковые массивы до2000 точек; подходит для зависимости от параметров.

Пример: `{"operation":"plot","kind":"function","expression":"sin(x)","lower":-6.3,"upper":6.3,"samples":400,"path":"C:/work/sine.png","title":"Синус","x_label":"x, рад","y_label":"sin(x)"}`.
Проверь созданный PNG. График — дискретная численная выборка, между точками возможны
разрывы/особенности, которые она не обнаруживает. Для выдачи вызови resource_publish
с полученным path; только его download_url подтверждает скачиваемый файл.
