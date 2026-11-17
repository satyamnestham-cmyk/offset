def classFactory(iface):
    from .autocad_offset import AutoCADOffsetPlugin
    return AutoCADOffsetPlugin(iface)
