from prusa.connect.printer.const import State

BASE_STATES = {State.READY, State.BUSY}
PRINTING_STATES = {State.PRINTING, State.PAUSED, State.FINISHED}

JOB_ONGOING_STATES = {State.PRINTING, State.PAUSED}
JOB_ENDING_STATES = BASE_STATES.union({State.FINISHED, State.ERROR})
