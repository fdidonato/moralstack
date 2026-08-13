"""Regression suite for the AIR-Bench / XSTest / CoCoNot cases that failed governance.

Every case in `cases.jsonl` is a prompt taken from a real measured run where the
delivered answer was wrong: either a benign request that was refused, or a harmful
one that had to stay refused (the controls). The runner replays them through the
governance proxy and checks both the decision **and** the delivered text.
"""
