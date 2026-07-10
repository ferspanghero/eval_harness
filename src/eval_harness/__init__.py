"""Behavioral eval harness for prompt/instruction files — a skill, a workflow, or any prompt file.

Runs the target file **in place** — its content becomes the run's system prompt — on co-located
fixtures and grades the produced artifacts (deterministic checks + LLM judge). See ``README.md`` for
usage and ``project_files/`` for the design.
"""
