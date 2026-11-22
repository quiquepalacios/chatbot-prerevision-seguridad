# Banco Andesur - Lineamientos de Seguridad para Arquitecturas de Aplicaciones y APIs

Código: LSA-2026 | Versión: 2.1 | Responsable: Gerencia de Seguridad de la Información y Ciberseguridad - Arquitectura de Seguridad
Ámbito: toda solución web, móvil o API desarrollada o contratada por el banco, incluidas las de terceros que procesen datos de clientes.

> Documento ficticio elaborado para el proyecto académico del Diplomado de AI Engineer (DMC). Banco Andesur no existe. Las políticas se basan en OWASP ASVS 5.0, OWASP API Security Top 10 2023, OWASP Top 10:2025, PCI DSS, el Reglamento SBS N.° 504-2021 y la Ley N.° 29733 de Protección de Datos Personales.

## Cómo usar este documento

- Cada política tiene un código (POL-XX) y reglas numeradas (POL-XX.N). Toda observación de seguridad debe citar la regla que incumple.
- "Debe" indica una obligación. "No debe" indica una prohibición.
- La sección "Revisión obligatoria" indica cuándo la solución no puede pasar a producción sin la aprobación expresa de Arquitectura de Seguridad. Una herramienta automática puede señalar estos puntos, pero nunca aprobarlos.
- Las excepciones se solicitan por escrito a Arquitectura de Seguridad, con plazo de vigencia y plan de remediación.

---

## POL-01 Autorización de acceso a recursos y funciones

**Objetivo:** que cada cliente o usuario interno solo pueda ver y operar sus propios recursos y las funciones de su rol.

- **POL-01.1** Todo endpoint que reciba un identificador de recurso (cuenta, tarjeta, vehículo, documento, orden, carrito, reporte) debe validar en el servidor que el recurso pertenece al usuario autenticado o que su rol le permite acceder.
- **POL-01.2** Usar identificadores aleatorios (UUID) o difíciles de adivinar no reemplaza la validación de la regla POL-01.1. Se considera que todo identificador puede filtrarse.
- **POL-01.3** No se deben usar identificadores correlativos expuestos al cliente para recursos con datos personales o financieros.
- **POL-01.4** Las funciones administrativas, de soporte o de backoffice deben validar el rol en el servidor. Ocultar una opción en el menú o en el front no es un control de seguridad.
- **POL-01.5** El rol o los permisos de un usuario no deben poder asignarse desde datos enviados por el propio usuario (por ejemplo, un campo `admin` o `rol` en el registro).
- **POL-01.6** Los permisos se otorgan por defecto denegados: lo que no está explícitamente permitido está prohibido.

**Revisión obligatoria:** endpoints que expongan datos de cuentas, tarjetas, documentos financieros o ubicación del cliente; cualquier función administrativa.

**Referencias:** OWASP API1:2023 (BOLA), API5:2023 (BFLA), OWASP A01:2025, ASVS 5.0 capítulo V8 (Autorización).

---

## POL-02 Autenticación de clientes

**Objetivo:** asegurar que solo el titular accede a su cuenta.

- **POL-02.1** Toda solución para clientes debe usar el servicio de autenticación central del banco (IAM corporativo). No se permiten registros ni logins propios por aplicación, ni siquiera de forma temporal.
- **POL-02.2** Los códigos OTP deben tener al menos 6 dígitos, vencer en un máximo de 5 minutos y bloquearse tras 5 intentos fallidos.
- **POL-02.3** Toda versión de un endpoint de autenticación o recuperación debe tener los mismos controles. Una versión anterior sin límite de intentos debe apagarse antes de publicar la nueva.
- **POL-02.4** La recuperación de contraseña no debe depender de preguntas de seguridad. Debe usar un factor de posesión registrado (app del banco o dispositivo enrolado).
- **POL-02.5** Los mensajes de error de login deben ser genéricos ("usuario o contraseña incorrectos") para no revelar si un usuario existe.
- **POL-02.6** Todo endpoint de login, OTP o recuperación debe tener límite de intentos por usuario, por dispositivo y por IP.

**Revisión obligatoria:** cualquier flujo de autenticación o recuperación que no use el IAM corporativo.

**Referencias:** OWASP API2:2023, A07:2025, ASVS 5.0 capítulo V6 (Autenticación), Reglamento SBS 504-2021 (requisitos de autenticación).

---

## POL-03 Autenticación reforzada para operaciones sensibles

**Objetivo:** proteger las operaciones que mueven dinero o permiten tomar control de la cuenta.

- **POL-03.1** Requieren un segundo factor en el momento de la operación, aunque el cliente ya haya iniciado sesión: transferencias y abonos a terceros, alta de beneficiarios, cambio de celular o email, aumento de límites, canjes por encima del umbral definido por Riesgos y cambio de contraseña.
- **POL-03.2** El cambio de número celular debe validarse por un canal distinto al número nuevo y notificarse al número anterior.
- **POL-03.3** El monto, la cuenta destino y el beneficiario que el cliente confirma deben ser exactamente los que se ejecutan. El servidor debe vincular el segundo factor a esos datos.

**Revisión obligatoria:** todo flujo nuevo que mueva dinero o puntos, o que cambie datos de contacto del cliente.

**Referencias:** Reglamento SBS 504-2021 (autenticación), OWASP API6:2023, ASVS 5.0 capítulo V6.

---

## POL-04 Tokens y sesiones

- **POL-04.1** Los tokens de acceso deben durar como máximo 15 minutos. Las sesiones largas se mantienen con refresh tokens rotativos.
- **POL-04.2** Debe existir un mecanismo para revocar tokens (cierre de sesión, cambio de contraseña, sospecha de fraude).
- **POL-04.3** Los tokens deben firmarse con algoritmos asimétricos aprobados (RS256 o ES256) y claves custodiadas en el vault corporativo. Están prohibidas las claves cortas o escritas en el código.
- **POL-04.4** En aplicaciones web, los tokens no deben guardarse en localStorage. Deben usarse cookies HttpOnly, Secure y SameSite.
- **POL-04.5** La validación de tokens debe hacerse en el API Gateway corporativo o con la librería estándar del banco, no con implementaciones propias por servicio.

**Referencias:** OWASP API2:2023, A07:2025, ASVS 5.0 capítulos V7 (Sesiones) y V9 (Tokens autocontenidos).

---

## POL-05 Comunicación entre servicios y cuentas de servicio

**Objetivo:** aplicar mínimo privilegio y confianza cero entre sistemas internos.

- **POL-05.1** Ningún servicio debe confiar en otro solo por estar en la red interna. Toda llamada entre servicios debe autenticarse (mTLS u OAuth client credentials).
- **POL-05.2** Las cuentas de servicio deben tener solo los permisos que su función necesita. Está prohibido usar perfiles generales para "avanzar más rápido".
- **POL-05.3** Los tokens de servicio deben tener vencimiento y alcance limitado. Están prohibidos los tokens fijos sin vencimiento.
- **POL-05.4** Un servicio no debe aceptar la identidad del cliente final desde un header o parámetro enviado por otro sistema (por ejemplo, `X-Cliente-DNI`). La identidad del cliente debe viajar en un token emitido por el IAM y verificarse en cada servicio.
- **POL-05.5** Cada servicio debe tener su propio usuario de base de datos, con acceso solo a su esquema.

**Revisión obligatoria:** toda cuenta de servicio con acceso al core bancario o a datos de clientes.

**Referencias:** NIST SP 800-207 (Zero Trust), NIST SP 800-204, OWASP API2:2023, API5:2023, A01:2025.

---

## POL-06 Operaciones monetarias e integración con el core

- **POL-06.1** El monto y la cuenta destino de un abono, devolución o transferencia deben obtenerse de registros del servidor, nunca de datos enviados por el cliente ni de campos editables de una orden.
- **POL-06.2** Toda operación que mueva dinero o puntos debe ser idempotente: debe llevar un identificador único de operación y el sistema destino debe rechazar duplicados. Un reintento por timeout no debe ejecutar la operación dos veces.
- **POL-06.3** Las operaciones de consulta de saldo, registro y descuento deben ejecutarse de forma atómica o con reserva de saldo, para evitar que dos solicitudes simultáneas usen el mismo saldo.
- **POL-06.4** Los límites de negocio (montos diarios, canjes, transferencias) deben validarse en el servidor. La validación en el front es solo para la experiencia de usuario.
- **POL-06.5** El estado de una orden u operación (por ejemplo, "pagado" o "devuelto") solo puede cambiarlo el sistema según su flujo, no un dato enviado por el cliente.

**Revisión obligatoria:** toda integración que escriba en el core bancario o en el sistema de Lealtad.

**Referencias:** OWASP API6:2023, API3:2023, A06:2025, ASVS 5.0 capítulo V2 (Validación y lógica de negocio).

---

## POL-07 Validación de entradas y archivos

- **POL-07.1** Todas las consultas a bases de datos (SQL y NoSQL) deben usar consultas parametrizadas o el ORM sin concatenar datos del usuario.
- **POL-07.2** Las APIs deben aceptar solo los campos esperados (lista de campos permitidos). Los campos adicionales se rechazan.
- **POL-07.3** El procesamiento de XML debe tener deshabilitadas las entidades externas (XXE). Se prefiere JSON para integraciones nuevas.
- **POL-07.4** Las expresiones regulares para validar datos deben estar revisadas contra ReDoS. No se deben copiar expresiones de fuentes no verificadas.
- **POL-07.5** La validación de archivos subidos (tipo real, tamaño, contenido) debe hacerse en el servidor. Los archivos se guardan fuera del directorio web y se analizan con antimalware.
- **POL-07.6** Los parámetros que controlan procesos internos (formatos de conversión, número de reintentos, comandos) no deben recibirse del cliente. Si son necesarios, se eligen de una lista cerrada en el servidor y tienen tope.
- **POL-07.7** Toda salida que muestre datos ingresados por el usuario debe codificarse para evitar XSS.

**Referencias:** OWASP A05:2025 (Inyección), API3:2023, API4:2023, ASVS 5.0 capítulos V1 y V5.

---

## POL-08 URLs externas, redirecciones y webhooks

- **POL-08.1** El servidor no debe llamar a URLs enviadas por el usuario. Las URLs de aliados y proveedores se registran en una configuración controlada por el banco.
- **POL-08.2** Cuando sea indispensable descargar un recurso externo, debe usarse una lista blanca de dominios exactos y bloquear direcciones internas.
- **POL-08.3** Las redirecciones deben validar el dominio exacto contra una lista blanca. Está prohibido validar con "contiene".
- **POL-08.4** Todo webhook entrante (pasarelas de pago, proveedores GPS, aliados) debe validar la firma del mensaje, la marca de tiempo (anti-replay) y, cuando sea posible, la IP de origen. Que la URL "no esté publicada" no es un control.
- **POL-08.5** Las respuestas de terceros deben validarse como cualquier dato externo antes de usarse.

**Revisión obligatoria:** todo webhook que cambie el estado de un pago u orden.

**Referencias:** OWASP API7:2023 (SSRF), API10:2023, A01:2025, A08:2025.

---

## POL-09 Exposición de datos e inventario de APIs

- **POL-09.1** Las respuestas de API deben devolver solo los campos que el consumidor necesita. Está prohibido devolver objetos completos de clientes (DNI, email, identificadores internos) para que el front los filtre.
- **POL-09.2** Los endpoints de depuración, prueba o recreación de datos (por ejemplo `_debug` o `createdb`) no deben existir en producción.
- **POL-09.3** La documentación de APIs (Swagger), las métricas y los paneles internos no deben estar accesibles desde internet.
- **POL-09.4** Está prohibido publicar carpetas de archivos, backups o contratos en rutas web. Los archivos se comparten por las herramientas corporativas.
- **POL-09.5** Toda API debe estar registrada en el inventario corporativo con su versión. Al publicar una versión nueva, la anterior debe tener fecha de apagado no mayor a 90 días y los mismos controles de seguridad mientras siga activa.
- **POL-09.6** Toda API expuesta a internet debe publicarse a través del API Gateway corporativo con rate limiting configurado.

**Referencias:** OWASP API3:2023, API8:2023, API9:2023, API4:2023, A02:2025.

---

## POL-10 Contraseñas, cifrado y secretos

- **POL-10.1** Las contraseñas solo pueden almacenarse con algoritmos aprobados para contraseñas (Argon2id o bcrypt). Están prohibidos el texto plano, MD5 y SHA sin sal.
- **POL-10.2** Los datos personales y financieros deben cifrarse en reposo con claves gestionadas por el banco.
- **POL-10.3** Los secretos (claves, contraseñas de BD, tokens) deben guardarse en el vault corporativo. Está prohibido guardarlos en el código, en archivos de configuración del repositorio o en docker-compose.
- **POL-10.4** Las comunicaciones deben usar TLS 1.2 o superior, también entre servicios internos.

**Referencias:** OWASP A04:2025 (Fallas criptográficas), ASVS 5.0 capítulos V11 (Criptografía) y V13 (Configuración).

---

## POL-11 Datos de tarjetas (PCI DSS)

- **POL-11.1** Ninguna aplicación fuera del entorno PCI certificado del banco debe almacenar el número completo de tarjeta (PAN). Para compras recurrentes se usa el servicio de tokenización.
- **POL-11.2** El código de seguridad (CVV) nunca se almacena, ni siquiera cifrado.
- **POL-11.3** El PAN, el CVV y la fecha de vencimiento no deben aparecer en logs, trazas, mensajes de error ni herramientas de monitoreo.
- **POL-11.4** Una solución que almacene, procese o transmita datos de tarjeta entra en el alcance PCI y debe ser evaluada por Seguridad antes de su diseño detallado.

**Revisión obligatoria:** toda solución que toque datos de tarjeta.

**Referencias:** PCI DSS v4.0 (requisitos 3 y 10), OWASP A04:2025.

---

## POL-12 Datos en ambientes no productivos

- **POL-12.1** Está prohibido usar datos reales de clientes en desarrollo, QA o certificación sin enmascarar o anonimizar.
- **POL-12.2** Los datos de prueba se generan de forma sintética o con el proceso corporativo de enmascaramiento.
- **POL-12.3** El acceso de proveedores externos a ambientes no productivos requiere contrato vigente, cuentas nominales y registro de accesos.
- **POL-12.4** Los ambientes no productivos no deben tener conectividad con sistemas productivos.

**Revisión obligatoria:** cualquier pedido de copia de datos de producción.

**Referencias:** Ley N.° 29733, Reglamento SBS 504-2021, NIST SP 800-218 (SSDF).

---

## POL-13 Proveedores, terceros y nube

- **POL-13.1** Todo tercero que reciba datos de clientes (talleres afiliados, comercios, fábricas de software, proveedores SaaS) debe pasar la evaluación de proveedores del banco y firmar cláusulas de protección de datos y seguridad, sin importar su tamaño o el volumen de datos.
- **POL-13.2** Al tercero solo se le envían los datos mínimos necesarios para su función (por ejemplo, un código de cita en vez de DNI y celular).
- **POL-13.3** Las integraciones con terceros deben usar credenciales propias por tercero, revocables, y canales autenticados.
- **POL-13.4** El uso de servicios en la nube o SaaS que almacenen o procesen datos de clientes requiere evaluación previa de Seguridad y de Riesgos, incluida la ubicación de los datos y la transferencia fuera del país.

**Revisión obligatoria:** todo nuevo tercero o servicio en la nube con acceso a datos de clientes.

**Referencias:** Reglamento SBS 504-2021 (proveedores y servicios en la nube), Ley N.° 29733, OWASP API10:2023.

---

## POL-14 Registro, monitoreo y manejo de errores

- **POL-14.1** Los eventos de seguridad (logins, fallos de autenticación, cambios de datos, operaciones monetarias, accesos denegados) deben registrarse y enviarse al SIEM corporativo.
- **POL-14.2** Los logs no deben contener contraseñas, tokens, datos de tarjeta ni cuerpos completos de solicitudes o respuestas con datos personales. Deben enmascarar DNI y números de cuenta.
- **POL-14.3** Los logs no deben ser accesibles por rutas web. Soporte accede a través de la herramienta corporativa de observabilidad.
- **POL-14.4** Los errores mostrados al usuario deben ser genéricos y con un código de referencia. El detalle técnico (stack trace, consultas) solo va al log interno.
- **POL-14.5** Toda solución debe definir alertas para patrones de abuso: intentos masivos de login u OTP, acceso a recursos de otros clientes y volumen anómalo de operaciones.

**Referencias:** OWASP A09:2025, A10:2025, ASVS 5.0 capítulo V16 (Registro y errores), PCI DSS requisito 10.

---

## POL-15 Finalidad, retención y entrega de datos personales

- **POL-15.1** Los datos personales solo pueden usarse para la finalidad informada al cliente. Usarlos para otra finalidad (por ejemplo, ubicación GPS para cobranzas) requiere evaluación legal y, cuando corresponda, consentimiento.
- **POL-15.2** Toda solución debe definir el plazo de retención de los datos que guarda y un proceso de borrado. Los datos de ubicación se conservan como máximo 90 días, salvo requerimiento legal.
- **POL-15.3** Los documentos con datos financieros (estados de cuenta, constancias) deben entregarse con enlaces firmados que venzan en un máximo de 24 horas y estén ligados al cliente.
- **POL-15.4** El cliente puede compartir un documento con terceros mediante una función que registre a quién y por cuánto tiempo, no reenviando enlaces sin vencimiento.

**Revisión obligatoria:** uso de datos de ubicación, biométricos o financieros para una finalidad nueva.

**Referencias:** Ley N.° 29733, Reglamento SBS 504-2021, OWASP API3:2023.
