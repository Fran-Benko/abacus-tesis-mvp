"""Paquete de auditoría detectable ante manipulación (H7).

La cadena de auditoría es **tamper-evident** (detectable ante manipulación), no
inmutable: sin checkpoint externo no se puede garantizar la detección de una
reescritura completa o de un truncado final coherente por parte del owner o un
superusuario. Este MVP no incluye KMS, firmas ni checkpoint externo.

Componentes:
- `canonical`: payload canónico y encadenamiento SHA-256.
- `append`: append transaccional y atómico a `audit_chain`.
- `verify`: verificación de la cadena desde el génesis.
- `cli`: interfaz de línea de comandos para verificar la cadena.
"""