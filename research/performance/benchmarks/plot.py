"""Matplotlib plot from genuine measured evidence only; never synthesizes data."""
import argparse
from ..schema import load
from .summarize import summarize

def candidates(rows,metric):
    return [x for x in summarize(rows) if x['metric']==metric and x['sample_count']>=5]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('--metric',required=True);p.add_argument('--output',required=True);p.add_argument('--validate-only',action='store_true');a=p.parse_args()
    data=candidates(load(a.input),a.metric)
    if not data:raise SystemExit('No genuine groups with at least5 independent trials; no comparison graph created')
    if len({x['unit'] for x in data})!=1:raise SystemExit('Mixed units forbidden')
    if a.validate_only:return
    try:import matplotlib.pyplot as plt
    except ImportError:raise SystemExit('Matplotlib unavailable: use a separately approved analysis environment; nothing installed') from None
    from pathlib import Path
    if Path(a.output).exists():raise SystemExit('Output already exists')
    fig,ax=plt.subplots();ax.bar([x['scenario']+'/'+x['component']+'/'+x['container'] for x in data],[x['mean'] for x in data],yerr=[x['standard_deviation'] for x in data])
    ax.set_ylabel(data[0]['unit']);ax.set_title(a.metric+' (trial means ± sample SD)');ax.tick_params(axis='x',rotation=45);fig.tight_layout();fig.savefig(a.output);plt.close(fig)
if __name__=='__main__':main()
