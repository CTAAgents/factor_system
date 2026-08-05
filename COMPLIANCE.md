# Open Source Compliance Guide

This document explains what is included in the open-source version of FTS,
what has been intentionally excluded, and how users can navigate the
compliance landscape when using this software.

## What Is Included ✅

| Category | Status | Description |
|---|---|---|
| Core Framework | ✅ Fully Open | L1/L2/L3 loop orchestration, contracts, pipeline |
| Evaluation Engine | ✅ Fully Open | 3-level evaluation chain, quality scorecard, audit |
| Verification Protocol | ✅ Fully Open | Locked Verifier with immutable judgment logic |
| Logic Review Suite | ✅ Fully Open | Ablation, SHAP, robustness, causal validation |
| Monitoring & Scheduling | ✅ Fully Open | Dashboard, Prometheus, APScheduler, watchdog |
| Engineering Docs | ✅ Fully Open | CLAUDE.md, HARNESS specs, architecture docs |
| Test Suite | ✅ Fully Open | 1700+ test cases, 99% coverage |
| Demo Seed Factors | ✅ Limited Demo | ~20-30 example factors for demonstration only |
| Synthetic Data Generator | ✅ Fully Open | Generate synthetic market data for testing |

## What Is Excluded ❌

| Category | Reason for Exclusion | Alternative |
|---|---|---|
| WorldQuant 101 Alphas | Third-party proprietary IP | Use public formula references; implement your own |
| GTJA 191 Alphas | Third-party copyrighted research | Obtain authorization from Guotai Junan Securities |
| Full Qlib Alpha158 Set | Only demo subset included | Download full set from Microsoft Qlib (Apache 2.0) |
| Real-time IC/ICIR Values | Confidential business information | Compute your own using the evaluation engine |
| Live Trading Signals | Confidential business information | Generate your own signals via the pipeline |
| Production Configurations | Security-sensitive | Create your own from the documented interfaces |
| API Keys / Credentials | Security-sensitive | Configure your own via environment variables |

## How to Build Your Own Seed Library

Since the full seed factor library is not included, here are legitimate
ways to build your own:

### Option 1: Public Academic Sources
- Many academic papers publish factor formulas (e.g., Fama-French factors,
  momentum, short-term reversal)
- Implement these formulas as FTS-compatible `FactorProgram` instances

### Option 2: Self-Developed Factors
- Design your own factors based on domain knowledge
- Use the LLM constructor (macro evolution) to generate initial candidates
- Let the evolution loop discover novel combinations

### Option 3: Licensed Third-Party Libraries
- Some vendors offer commercially licensed factor libraries
- Ensure your license permits use within automated trading systems

### Option 4: Qlib Alpha158 (Apache 2.0)
- Full implementation available at `https://github.com/microsoft/qlib`
- Compatible with Apache 2.0 license terms
- Can be loaded via the `demo_seeds.py` interface pattern

## Using Third-Party Data

FTS supports multiple data sources through its adapter layer:

| Source | License Consideration | Recommendation |
|---|---|---|
| AKShare | MIT License | ✅ Safe for research and personal use |
| DuckDB (self-hosted) | MIT License | ✅ Store your own cleaned data |
| Wind Financial Terminal | Commercial license required | ⚠️ Verify your firm's license terms |
| TQ (TianQin) | Commercial license required | ⚠️ Verify your firm's license terms |
| iFinD | Commercial license required | ⚠️ Verify your firm's license terms |

## Frequently Asked Questions

**Q: Can I use FTS for live trading?**
A: FTS is a research framework that generates trading signals. Live trading
requires additional infrastructure for order execution, risk management, and
compliance monitoring. Users are responsible for ensuring their complete
system meets regulatory requirements.

**Q: Do I need permission to use the demo seed factors?**
A: The demo seeds consist of publicly known basic factors (moving averages,
volatility measures, etc.) and a small subset of Qlib Alpha158 (Apache 2.0).
They are safe for research use. For commercial deployment, verify each
factor's provenance.

**Q: Can I contribute my own factor implementations?**
A: Yes, provided you own the intellectual property or have the right to
contribute it. We recommend including attribution and license information
in your contribution.

**Q: How do I cite this project?**
A: If you use FTS in academic work, please cite:

CTAAgents. (2026). FTS: An AI-Native End-to-End Factor Trading System.
`https://github.com/CTAAgents/factor_system`

---

*For questions about compliance, please open an issue on GitHub or contact
the maintainers.*
