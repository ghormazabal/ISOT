# Plantillas de sitio

Cada carpeta es una plantilla para el wizard «Nuevo sitio»: solo la
topología (`topology/index.yaml`: rangos, subnets y VLANs). El wizard la copia,
le asigna el id y el rango del sitio nuevo, y genera fabric, clústeres y
devices.

- `edge/`: sitio pequeño (MGMT OOB e in-band, servicios edge, DCI opcional).
- `cloud/`: nube privada (storage, gestión, plataforma, apps, DCI opcional).

Un repositorio de datos puede tener su propia carpeta `site-templates/<id>/`
con la misma estructura: tiene prioridad sobre la plantilla del mismo id que
trae la herramienta.
