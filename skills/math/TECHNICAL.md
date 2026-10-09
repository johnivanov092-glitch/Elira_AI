# Прикладные и IT-расчёты

`operation=technical`, все параметры на верхнем уровне, ставки/нагрузки задаёт пользователь.
| action | Параметры |
| --- | --- |
| cidr | cidr строгая сеть IPv4/IPv6: например 192.168.1.0/24; адрес хоста с битами за маской отвергается. Возвращает маску/диапазоны/число адресов. IPv4 исключает network+broadcast до /30, IPv6 — subnet-router anycast до /126; /31,/32,/127,/128 без вычитания. |
| subnets | cidr и prefix новой подсети, максимум1024 подсети. |
| raid | disks1..10000, disk_size>0, disk_unit (TB), raid строка 0/1/5/6/10, reserve_percent0..<100 (0). Одинаковые диски, RAID1 зеркалирует все диски, RAID10 чётное≥4. Не учитывает FS/controller overhead; RAID не является резервной копией. |
| growth | initial≥0, size_unit (GB), growth_percent≥0 за период, periods0..1000. Сложный рост. |
| backup | size≥0, size_unit (GB), copies1..1000 (1), retention_days1..3660, daily_change_percent0..100. Одна полная + ежедневные инкрементальные, без сжатия/дедупликации. |
| transfer | size≥0,size_unit(GB), speed>0,speed_unit(Mbit/s), efficiency доля0..< =1 (1). Если скорость уже эффективная, оставь efficiency=1. |
| power | voltage>0 вV,current≥0 вA, power_factor0..1 (1), phases1или3 (1). Для3 фаз — линейное напряжение и симметричная нагрузка. |
| current | power≥0,power_unit(W),voltage>0 вV. DC/коэффициент мощности1. |
| voltage | power≥0,power_unit(W),current>0 вA. DC/коэффициент мощности1. |
| ohm | voltage≥0 вV,resistance>0 вΩ → токA. |
| energy | power≥0,power_unit(W),hours≥0,headroom_percent≥0 (0) → kWh и требуемая мощностьW. |
| surveillance | cameras1..100000,bitrate≥0,bitrate_unit(Mbit/s),days≥0,duty_cycle0..1 (1). Постоянный заданный битрейт; без скрытого сжатия/запаса. |

Примеры:
`{"operation":"technical","action":"transfer","size":100,"size_unit":"GB","speed":100,"speed_unit":"Mbit/s","efficiency":"0.8"}`.
`{"operation":"technical","action":"raid","raid":"6","disks":8,"disk_size":4,"disk_unit":"TB","reserve_percent":10}`.
Проверь единицы GB/GiB, MB/s/Mbit/s; результат содержит формулу, подстановку и единицы.
