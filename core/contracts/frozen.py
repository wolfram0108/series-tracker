"""The base of the models that are handed over as they are: no field of one is set again, so whoever holds it may rely on it.

A list a field holds is a tuple (a model is built from a list all the same); a dictionary stays a dictionary."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)
