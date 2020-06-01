from typing import Callable
from time import sleep, time


def run_slowly_die_fast(should_loop: Callable[[], bool], check_exit_every_sec, run_every_sec, to_run,
                        *arg_getters, **kwarg_getters):
    """
    Lets say you run something every minute, but you want to quit your program faster

    This lets you do that. there is lots of getter functions as params. If they were passed by value,
    even the should loop would never change resulting in an infinite loop. Getters seem like a nice way to pas
    by reference
    """

    last_refreshed = 0
    while should_loop():
        if time() - last_refreshed > run_every_sec:

            args = []
            for getter in arg_getters:
                args.append(getter())

            kwargs = {}
            for name, getter in kwarg_getters:
                kwargs[name] = getter()

            to_run(*args, **kwargs)
            last_refreshed = time()
        sleep(min(check_exit_every_sec, (last_refreshed + run_every_sec) - time()))