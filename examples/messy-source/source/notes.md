# Notas lab1 (ejemplo ficticio)

Apuntes sueltos del equipo. Formato libre.

## Redes

- VLAN 10 = OOB / BMC, red 10.60.0.0/24, gw 10.60.0.1
- vlan 100 -> gestión de hosts (10.60.1.0/24), gateway .1
- VLAN 200 storage 10.60.2.0/24, sin gateway (L2 puro)
- la 300 era para pruebas, ya no se usa (¿borrar?)

## Kubernetes

Cluster lab1-k8s: node01 es el control plane, node02 y node03 workers.
Pod CIDR 10.244.0.0/16. El service CIDR no lo tengo anotado.

## Pendientes

- node03: le cambiamos la IP de gestión a 10.60.1.23 (confirmar)
- node04 todavía sin cablear
