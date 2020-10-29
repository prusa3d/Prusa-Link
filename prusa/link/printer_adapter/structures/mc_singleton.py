class MCSingleton(type):
    def __init__(cls, name, bases, dic):
        cls.__instance = None
        cls.get_instance = lambda: cls.__instance
        super().__init__(name, bases, dic)

    def __call__(cls, *args, **kwargs):
        if cls.__instance is not None:
            raise RuntimeError("There can be only one singleton in existance")
        else:
            instance = cls.__new__(cls)
            instance.__init__(*args, **kwargs)
            cls.__instance = instance
            return instance
