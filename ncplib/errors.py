__all__ = (
    "DecodeError",
    "CommandError",
    "DecodeWarning",
    "CommandWarning",
)


class CommandMixin:

    def __init__(self, message, detail, code):
        super().__init__("{packet_type} {field_name} '{detail}' (code {code})".format(
            packet_type=message.packet_type,
            field_name=message.field_name,
            detail=detail,
            code=code,
        ))
        self.message = message
        self.detail = detail
        self.code = code


# Errors.

class DecodeError(Exception):

    pass


class CommandError(CommandMixin, Exception):

    pass


# Warnings.

class DecodeWarning(Warning):

    pass


class CommandWarning(CommandMixin, Warning):

    pass
