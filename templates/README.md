# Plantillas incluidas en la herramienta

- `cluster-templates/`: catálogo de cluster-templates genéricos. Si un clúster
  del repo de datos declara un `template_ref` que el repo no tiene, se resuelve
  aquí (o en las carpetas `templates:` de la configuración). Un template del
  mismo id en `cluster-templates/` del repo de datos tiene prioridad. Al crear
  un sitio con el wizard, el template elegido se copia al repo de datos para
  que quede versionado junto al inventario.
- `sites/`: plantillas de sitio para el wizard «Nuevo sitio» y para
  `isot init --scaffold`.
