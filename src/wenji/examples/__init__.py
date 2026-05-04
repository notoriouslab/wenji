"""Wheel-bundled corpus example data.

Domain-specific entity dictionaries and intent keyword lists shipped as
namespace packages so that ``EntityScorer.from_sources(["example:<name>"])``
and ``IntentClassifier.from_sources(["example:<name>"])`` can discover
them via ``importlib.resources``.

Each example is a domain reference (e.g., ``corpus_christian`` for
Chinese Christian-knowledge corpora), NOT a wenji framework requirement.
Users targeting other domains can omit loading examples and still run
the framework.
"""
