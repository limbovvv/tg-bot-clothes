class ServiceError(Exception):
    pass


class ActiveGiveawayExists(ServiceError):
    pass


class GiveawayNotFound(ServiceError):
    pass


class EntryExists(ServiceError):
    pass
