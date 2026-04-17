# Contributing to MoralStack

Thanks for your interest in contributing. MoralStack is a research-stage project and contributions are welcome.

## How to contribute

**Report a bug**
Open an issue with a clear description of the problem, the expected behavior, and the steps to reproduce it. If you have a benchmark trace, attach it.

**Suggest an improvement**
Open an issue describing the problem you want to solve before writing code. This avoids wasted effort if the change doesn't align with the project direction.

**Submit a pull request**
1. Fork the repository and create a branch from `main`
2. Make your changes with clear, focused commits
3. Run the benchmark suite before submitting: `python scripts/benchmark_moralstack.py`
4. Open a PR with a description of what you changed and why

## Areas where contributions are most useful

- **New domain overlays** — see [Creating Domain Overlays](docs/creating_overlays.md) for a step-by-step guide. Validate your overlay before submitting: `moralstack-validate-overlay moralstack/constitution/data/overlays/your_domain.yaml`
- Benchmark coverage (additional edge cases or adversarial prompts)
- Latency optimizations
- Documentation improvements

## Code style

The project uses Python 3.11+. Run pre-commit hooks before pushing:

```bash
pre-commit run --all-files
```

## Questions

Open an issue or start a discussion on GitHub.
