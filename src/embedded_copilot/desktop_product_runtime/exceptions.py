class DesktopProductError(Exception):
    pass


class ProductSessionRejected(DesktopProductError):
    pass


class ProductConflict(DesktopProductError):
    pass


class ProductUnavailable(DesktopProductError):
    pass
