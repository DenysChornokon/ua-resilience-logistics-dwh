import csv

import pytest
from scripts.plot_benchmark_results import summarize


def test_ignores_warmup_and_converts_milliseconds(tmp_path):
    path = tmp_path/'measurements.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['phase','size','method','execution_ms'])
        writer.writeheader()
        for method in ('baseline','range'):
            writer.writerow({'phase':'warmup','size':10000,'method':method,'execution_ms':9999})
            for n in range(5):
                writer.writerow({'phase':'measured','size':10000,'method':method,'execution_ms':1000+n})
    sizes, groups=summarize(path)
    assert sizes == [10000]
    assert groups[(10000,'baseline')] == [1,1.001,1.002,1.003,1.004]


def test_rejects_incomplete_measurements(tmp_path):
    path=tmp_path/'measurements.csv'
    path.write_text('phase,size,method,execution_ms\nmeasured,10000,baseline,1\n',encoding='utf-8')
    with pytest.raises(ValueError,match='Unexpected number'):
        summarize(path)
