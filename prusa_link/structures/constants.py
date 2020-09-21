from prusa_link.structures.model_classes import States

BASE_STATES = {States.READY, States.BUSY}
PRINTING_STATES = {States.PRINTING, States.PAUSED, States.FINISHED}

JOB_ONGOING_STATES = {States.PRINTING, States.PAUSED}
JOB_ENDING_STATES = BASE_STATES.union({States.FINISHED, States.ERROR})