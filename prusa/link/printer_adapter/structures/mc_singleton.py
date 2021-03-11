"""Contains implementation of the MCSingleton class"""


class MCSingleton(type):
    """
    Classes that use this metaclass are singletons
    """
    def __init__(cls, name, bases, dic):
        cls.__instance = None
        cls.get_instance = lambda: cls.__instance
        super().__init__(name, bases, dic)

    def __call__(cls, *args, **kwargs):
        if cls.__instance is not None:
            raise RuntimeError("There can be only one singleton in existence")

        instance = cls.__new__(cls)
        instance.__init__(*args, **kwargs)
        cls.__instance = instance
        return instance
