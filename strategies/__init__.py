
from .strat_dexs import DexScreenerStrategy
# from .strat_gmgn import GmgnStrategy

STRATEGIES = {
    DexScreenerStrategy.name: DexScreenerStrategy,
    # "gmgn": GmgnStrategy,
}
