"""Independent known answers and failure boundaries through the public MATH entry."""
from pathlib import Path

import pytest

from app.core.skill_modules import load_skill_module


@pytest.fixture(scope="module")
def math_skill():
    return load_skill_module("math", "calculate.py")


@pytest.mark.parametrize("payload,path,expected", [
    ({"operation":"geometry","shape":"triangle","a":3,"b":4,"c":5}, "values.area.exact", "6"),
    ({"operation":"geometry","shape":"box","length":2,"width":3,"height":4}, "values.volume.exact", "24"),
    ({"operation":"geometry","shape":"distance","point1":[0,0],"point2":[3,4]}, "values.distance.exact", "5"),
    ({"operation":"geometry","shape":"polygon","points":[[0,0],[2,0],[2,3],[0,3]]}, "values.area.exact", "6"),
    ({"operation":"geometry","shape":"material","area":10,"piece_area":3,"waste_percent":10}, "values.pieces.exact", "4"),
    ({"operation":"trigonometry","action":"cos","value":60,"angle_unit":"deg"}, "values.result.exact", "1/2"),
    ({"operation":"trigonometry","action":"triangle_angles","a":3,"b":4,"c":5,"angle_unit":"deg"}, "values.C.exact", "90"),
    ({"operation":"statistics","values":[1,2,3,4],"sample":True,"weights":[1,1,1,3],"y":[2,4,6,8]}, "values.slope.exact", "2"),
    ({"operation":"statistics","values":[1,2,3,4],"percentile":25}, "values.percentile.exact", "7/4"),
    ({"operation":"statistics","values":[1,2,3,4],"weights":[1,1,1,3]}, "values.weighted_mean.exact", "3"),
    ({"operation":"probability","action":"binomial","n":10,"k":2,"p":"1/2"}, "values.result.exact", "45/1024"),
    ({"operation":"probability","action":"conditional","a":"0.5","b":"0.4","intersection":"0.2"}, "values.result.exact", "1/2"),
    ({"operation":"probability","action":"expectation","values":[0,10],"probabilities":["0.8","0.2"]}, "values.result.exact", "2"),
    ({"operation":"linear_algebra","action":"determinant","matrix":[[1,2],[3,4]]}, "values.result.exact", "-2"),
    ({"operation":"linear_algebra","action":"solve","matrix":[[1,1],[2,2]],"rhs":[1,3]}, "values.result.consistent", False),
    ({"operation":"linear_algebra","action":"norm","vector":[3,4]}, "values.result.exact", "5"),
    ({"operation":"limit","expression":"sin(x)/x","point":0}, "values.result.exact", "1"),
    ({"operation":"summation","expression":"1/2**x","lower":1}, "values.result.exact", "1"),
    ({"operation":"inequality","relations":["x**2<4"]}, "values.solution", "(-2 < x) & (x < 2)"),
    ({"operation":"optimize","action":"linear","objective":[1,2],"matrix":[[-1,-1]],"rhs":[-3]}, "values.minimum.exact", "3"),
    ({"operation":"optimize","action":"polynomial","expression":"x**2","lower":-1,"upper":2}, "values.maximum.value.exact", "4"),
    ({"operation":"bits","action":"xor","value":10,"other":12}, "values.decimal", "6"),
    ({"operation":"bits","action":"not","value":0,"width":8}, "values.decimal", "255"),
    ({"operation":"bits","action":"base","value":"FF","base":16,"target_base":2}, "values.representation", "11111111"),
    ({"operation":"discrete","action":"union","left":["a","b"],"right":["b","c"]}, "values.result", ["a","b","c"]),
    ({"operation":"discrete","action":"shortest_path","nodes":["a","b","c"],"edges":[["a","c",9],["a","b",2],["b","c",3]],"source":"a","target":"c"}, "values.result.path", ["a","b","c"]),
    ({"operation":"dates","action":"shift","start":"2024-01-31","months":1,"month_end":"clamp"}, "values.date", "2024-02-29"),
    ({"operation":"dates","action":"duration","start":"2024-03-31T01:30:00+01:00","end":"2024-03-31T03:30:00+02:00"}, "values.seconds", "3600"),
    ({"operation":"dates","action":"business_add","start":"2026-10-09","days":1,"calendar":{"country":"explicit-test","years":[2026],"holidays":["2026-10-12"]}}, "values.date", "2026-10-13"),
    ({"operation":"technical","action":"cidr","cidr":"192.0.2.0/24"}, "values.usable_addresses", 254),
    ({"operation":"technical","action":"cidr","cidr":"192.0.2.0/31"}, "values.usable_addresses", 2),
    ({"operation":"technical","action":"cidr","cidr":"2001:db8::/128"}, "values.usable_addresses", 1),
    ({"operation":"technical","action":"raid","raid":"6","disks":6,"disk_size":2,"disk_unit":"TB","reserve_percent":10}, "values.budget_bytes.exact", "7200000000000"),
    ({"operation":"technical","action":"transfer","size":1,"size_unit":"GB","speed":100,"speed_unit":"Mbit/s","efficiency":"0.8"}, "values.seconds.exact", "100"),
    ({"operation":"technical","action":"power","voltage":230,"current":10,"power_factor":"0.9"}, "values.watts.exact", "2070"),
    ({"operation":"technical","action":"energy","power":500,"hours":24}, "values.kwh.exact", "12"),
    ({"operation":"technical","action":"surveillance","cameras":4,"bitrate":2,"days":1}, "values.bytes.exact", "86400000000"),
    ({"operation":"technical","action":"backup","size":100,"size_unit":"GB","daily_change_percent":10,"retention_days":7,"copies":2}, "values.bytes.exact", "320000000000"),
    ({"operation":"compound_interest","principal":1000,"rate_percent":10,"periods":2}, "values.total.exact", "1210"),
    ({"operation":"depreciation","cost":1000,"salvage":100,"life":3,"period":2}, "values.book_value.exact", "400"),
    ({"operation":"break_even","fixed_cost":1000,"unit_price":30,"variable_cost":10}, "values.whole_units.exact", "50"),
    ({"operation":"dimensions","expression":"U*I","variables":{"U":"V","I":"A"},"expected_unit":"W"}, "values.compatible", True),
    ({"operation":"dimensions","expression":"d/t","variables":{"d":"m","t":"s"},"expected_unit":"W"}, "values.compatible", False),
    ({"operation":"evaluate","expression":"0.12345678901234567890123456789"}, "result.exact", "12345678901234567890123456789/100000000000000000000000000000"),
    ({"operation":"evaluate","expression":"2.675","places":2}, "result.approximate", True),
])
def test_known_results(math_skill, payload, path, expected):
    response = math_skill.run_request(payload)
    assert response["ok"], response
    result = response["result"]
    for key in path.split("."):
        result = result[key]
    assert result == expected


@pytest.mark.parametrize("payload", [
    {"operation":"geometry","shape":"triangle","a":1,"b":2,"c":3},
    {"operation":"geometry","shape":"circle","radius":-1},
    {"operation":"geometry","shape":"circle","radius":1,"unit":"s"},
    {"operation":"geometry","shape":"polygon","points":[[0,0],[2,2],[0,2],[2,0]]},
    {"operation":"trigonometry","action":"tan","value":90,"angle_unit":"deg"},
    {"operation":"trigonometry","action":"asin","value":2},
    {"operation":"statistics","values":[]},
    {"operation":"statistics","values":[True]},
    {"operation":"statistics","values":[1],"sample":True},
    {"operation":"statistics","values":[1,1],"y":[2,3]},
    {"operation":"statistics","values":[1,2],"weights":[0,0]},
    {"operation":"probability","action":"conditional","a":"0.5","b":0,"intersection":0},
    {"operation":"probability","action":"union","a":"0.9","b":"0.9","intersection":0},
    {"operation":"linear_algebra","action":"inverse","matrix":[[1,2],[2,4]]},
    {"operation":"linear_algebra","action":"multiply","matrix":[[1,2]],"other":[[1,2]]},
    {"operation":"nsolve","expression":"1/x","bracket":[-1,1]},
    {"operation":"bits","action":"not","value":-1},
    {"operation":"discrete","action":"topological","nodes":["a","b"],"edges":[["a","b"],["b","a"]]},
    {"operation":"dates","action":"shift","start":"2023-01-31","months":1},
    {"operation":"dates","action":"business_days","start":"2026-12-31","end":"2027-01-02","calendar":{"country":"test","years":[2026],"holidays":[]}},
    {"operation":"dates","action":"business_days","start":"2026-10-09","end":"2026-10-12"},
    {"operation":"technical","action":"raid","raid":"10","disks":5,"disk_size":1},
    {"operation":"technical","action":"transfer","size":1,"speed":0},
    {"operation":"technical","action":"subnets","cidr":"10.0.0.0/8","prefix":32},
    {"operation":"unit_convert","value":1,"from_unit":"kg","to_unit":"m"},
    {"operation":"dimensions","expression":"d+t","variables":{"d":"m","t":"s"},"expected_unit":"m"},
    {"operation":"dimensions","expression":"d-d+t-t","variables":{"d":"m","t":"s"},"expected_unit":"m"},
    {"operation":"compound_interest","principal":1000,"rate_percent":-100,"periods":2},
    {"operation":"break_even","fixed_cost":100,"unit_price":10,"variable_cost":10},
    {"operation":"evaluate","expression":"__import__('os')"},
    {"operation":"evaluate","expression":"1/0"},
    {"operation":"evaluate","expression":"1","places":True},
])
def test_invalid_inputs_never_claim_success(math_skill, payload):
    response = math_skill.run_request(payload)
    assert response["ok"] is False, response
    assert "result" not in response


def test_dates_handle_dst_and_overlap_explicitly(math_skill):
    pytest.importorskip("tzdata")
    for start in ("2024-03-31T02:30:00", "2024-10-27T02:30:00"):
        result = math_skill.run_request({"operation":"dates","action":"timezone","start":start,
                                         "zone":"Europe/Berlin","target_zone":"UTC"})
        assert result["ok"] is False
    response = math_skill.run_request({"operation":"dates","action":"overlap","intervals":[
        {"start":"2026-10-09T09:00:00+05:00","end":"2026-10-09T18:00:00+05:00"},
        {"start":"2026-10-09T09:00:00+02:00","end":"2026-10-09T17:00:00+02:00"}]})
    assert response["result"]["values"]["seconds"] == "21600"


@pytest.mark.parametrize("payload,path,expected", [
    ({"operation":"evaluate","expression":"round(2.5)"}, "result.decimal", "3"),
    ({"operation":"evaluate","expression":"123456789012345678901234567890"}, "result.decimal", "123456789012345678901234567890"),
    ({"operation":"solve","expression":"x=x"}, "solution_set", "Complexes"),
    ({"operation":"optimize","action":"linear","objective":[1],"equal_matrix":[[1]],"equal_rhs":[2]}, "values.minimum.exact", "2"),
    ({"operation":"optimize","action":"linear","objective":[1,1],"bounds":[[-2,-1],[-3,-2]]}, "values.minimum.exact", "-5"),
    ({"operation":"optimize","action":"linear","goal":"max","objective":[3,2],"matrix":[[1,1]],"rhs":[4]}, "values.maximum.exact", "12"),
    ({"operation":"optimize","action":"linear","objective":[1,2]}, "values.minimum.exact", "0"),
    ({"operation":"nsolve","expression":"x*x-2","bracket":[1,2],"places":2}, "values.result.root.decimal", "1.41"),
    ({"operation":"technical","action":"current","power":1,"power_unit":"kW","voltage":10}, "values.current.exact", "100"),
    ({"operation":"dates","action":"duration","start":"2026-01-01T00:00:00Z","end":"2026-01-01T01:00:00Z"}, "values.seconds", "3600"),
    ({"operation":"dates","action":"duration","start":"1900-01-01T00:00:00Z","end":"2200-01-01T00:00:00.000001Z"}, "values.seconds", "9467107200.000001"),
    ({"operation":"dates","action":"business_add","start":"2026-10-09","days":1,"calendar":{"country":"test","years":[2026],"holidays":[],"working_dates":["2026-10-10"]}}, "values.date", "2026-10-10"),
    ({"operation":"percent_of","part":1,"whole":128}, "percent", "0.7813"),
    ({"operation":"unit_convert","value":1,"from_unit":"m/s","to_unit":"km/h"}, "exact", "18/5"),
    ({"operation":"unit_convert","value":1,"from_unit":"km/h","to_unit":"m/s","places":2}, "result", "0.28"),
])
def test_review_regressions(math_skill, payload, path, expected):
    test_known_results(math_skill, payload, path, expected)


@pytest.mark.parametrize("payload", [
    {"operation":"evaluate","expression":"1//0"},
    {"operation":"evaluate","expression":"0xFF"},
    {"operation":"evaluate","expression":"binomial(1000000,500000)"},
    {"operation":"evaluate","expression":"1e999999"},
    {"operation":"evaluate","expression":"(10**1000)**1000"},
    {"operation":"unit_convert","value":-1,"from_unit":"K","to_unit":"C"},
    {"operation":"unit_convert","value":"1e999999","from_unit":"m","to_unit":"cm"},
    {"operation":"probability","action":"intersection","a":"0.5","b":"0.5","intersection":"0.1","independent":True},
    {"operation":"technical","action":"current","power":1,"voltage":10,"phases":3},
    {"operation":"dimensions","expression":"d+","variables":{"d":"m"},"expected_unit":"m"},
])
def test_review_invalid_inputs(math_skill, payload):
    response = math_skill.run_request(payload)
    assert response["ok"] is False


def test_periodic_equation_reports_infinite_solution_set(math_skill):
    result = math_skill.run_request({"operation":"solve","expression":"sin(x)=0"})["result"]
    assert "ImageSet" in result["solution_set"]
    assert "решений нет" not in result["note"]
