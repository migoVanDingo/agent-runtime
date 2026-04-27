from enum import Enum


class IdPrefix(str, Enum):
    SESSION  = "SESS"
    PLAN     = "PLAN"
    STEP     = "STEP"
    ARTIFACT = "ARTI"
