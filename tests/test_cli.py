import json

import pytest

from optiswitch import __version__
from optiswitch.cli import CliError, main, parse_size, run
from optiswitch.workload import BimodalSize, FixedSize, LogNormalSize


def test_run_text_and_json():
    out = run(["run", "--hosts", "6", "--flows", "300", "--seed", "2", "--check"])
    assert "300/300 completed" in out and "invariants:         ok" in out
    js = json.loads(run(["run", "--hosts", "6", "--flows", "300", "--seed", "2", "--json"]))
    assert js["completed"] == 300 and js["scheduler"] == "fifo"


def test_run_with_files(tmp_path):
    topo = tmp_path / "t.json"
    trace = tmp_path / "f.jsonl"
    assert "8 hosts" in run(["gen-topology", "--hosts", "8", "--reconfig-ns", "10000", "--out", str(topo)])
    assert "wrote 400 flows" in run(["gen-workload", "--hosts", "8", "--flows", "400", "--pattern", "permutation",
                                    "--size", "bimodal:10000:1000000:0.2", "--out", str(trace)])
    assert "400 flows" in run(["validate-trace", str(trace), "--hosts", "8"])
    fcsv, pcsv = tmp_path / "flows.csv", tmp_path / "ports.csv"
    out = run(["run", "--topology", str(topo), "--trace", str(trace), "--flows-csv", str(fcsv), "--ports-csv", str(pcsv)])
    assert "400/400" in out
    assert len(fcsv.read_text().splitlines()) == 401
    assert len(pcsv.read_text().splitlines()) == 9


def test_parse_size():
    assert parse_size("fixed:100") == FixedSize(100)
    assert parse_size("lognormal:5000") == LogNormalSize(5000.0, 1.0)
    assert parse_size("lognormal:5000:0.3") == LogNormalSize(5000.0, 0.3)
    assert parse_size("bimodal:1:2") == BimodalSize(1, 2, 0.1)
    for bad in ("fixed", "fixed:abc", "gamma:1", "bimodal:1"):
        with pytest.raises(CliError):
            parse_size(bad)


def test_schedulers_and_version(capsys):
    assert "fifo" in run(["schedulers"])
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_exit_codes(capsys, tmp_path):
    assert main(["schedulers"]) == 0
    assert main(["run", "--scheduler", "nope"]) == 1
    assert "unknown scheduler" in capsys.readouterr().err
    assert main(["run", "--size", "bogus"]) == 2
    assert main(["validate-trace", str(tmp_path / "missing.jsonl")]) == 1
    assert main(["run", "--trace", str(tmp_path / "missing.jsonl")]) == 1
    (tmp_path / "bad.jsonl").write_text('{"id":0,"src":0,"dst":40,"size_bytes":1,"arrival_ns":0}\n')
    assert main(["run", "--hosts", "4", "--trace", str(tmp_path / "bad.jsonl")]) == 1
