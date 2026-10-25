# Gestor de perfumes — clientes en modo SOLO LECTURA

El gestor permite consultar los perfumes de cada origen y copiar los seleccionados a la biblioteca local. **El envío y todas las escrituras en clientes están bloqueados desde el código.** ALLOW_CLIENT_WRITES y allow_write no pueden levantar el bloqueo.

## Abrir el panel

```bash
cd /home/nico/Escritorio/PERFUMES/gestor-perfumes
.venv/bin/python app.py
```

Abrí http://127.0.0.1:5055. Para detenerlo: Ctrl+C. Si tenés que instalarlo de nuevo: `python3 -m venv .venv`, `source .venv/bin/activate` y `pip install -r requirements.txt`.

## Biblioteca local

## Selecciones por cliente, precios y preparación para otro VPS

En **Lista del cliente**, cada renglón tiene un botón **Buscar en maestra**. Abre una ventana con búsqueda en vivo por nombre o marca, fotos y acceso a la ficha. Podés escribir una parte del nombre, incluso si no apareció entre las sugerencias iniciales. **Elegir coincidencia** vincula ese perfume al renglón y cierra la ventana sin recargar la página. Cada renglón admite una coincidencia y **Quitar elección** permite cambiarla. El botón **Ver maestra en una ventana** sirve para explorar toda la biblioteca sin salir de la lista. La búsqueda muestra hasta 50 resultados por consulta y contempla diferencias menores de escritura; no fusiona variantes ni las elige automáticamente.

Después de **Revisar selección con estos precios**, se conservan las líneas originales asociadas a cada perfume para que puedas cotejar sus precios. Si varios renglones eligen el mismo perfume de la maestra, la selección final lo contiene una sola vez.

La lista muestra contadores en vivo: **Pedidos en tu lista**, **Renglones con coincidencia elegida**, **Perfumes únicos seleccionados**, **A cargar a mano**, **Pendientes de revisar** y **Fuera de esta selección**. «Fuera» es la suma de manuales y pendientes; hay un desplegable con sus nombres y precios originales. La casilla **Cargar a mano / dejar fuera** confirma que ese renglón se resolverá manualmente y quita su coincidencia. Elegir luego un perfume lo vuelve a incorporar. Las sugerencias no cuentan como coincidencias confirmadas hasta que las seleccionás. Un perfume elegido en varios renglones cubre esos pedidos, pero se cuenta una sola vez entre los productos únicos.

El resumen también aparece al revisar precios y queda guardado con las nuevas selecciones del cliente, incluida la lista de pendientes/manuales, para medir lo que falta después de cerrar el programa. Esto mide la cobertura de la lista; no afirma cuántos INSERT se ejecutarán, porque todavía debe comprobarse qué productos existen en el destino.

En cada renglón de la lista del cliente, junto a las coincidencias y al nombre/precio original, cargá el **precio minorista** y los **mililitros**, precargados en 100. Podés hacerlo mientras elegís la coincidencia, incluso si la buscás desde el modal; cambiar de coincidencia no borra esos campos. Después presioná **Revisar selección con estos precios**: los valores pasan a la revisión y se conservan al guardar. El stock previsto es 11. Los precios admiten `45000`, `45.000` o `45.000,50`; cero indica que no asignaste precio. La lista original sigue disponible como referencia; los precios no se asignan automáticamente a coincidencias aproximadas. Si dos renglones eligen el mismo perfume con precios o tamaños distintos, el gestor pide corregirlos antes de continuar para no perder un valor silenciosamente.

Completá el nombre del cliente y presioná **Guardar selección con precios para este cliente**. No basta con abrir la revisión: ese botón confirma el guardado. Se guarda en `instance/biblioteca.db`, junto con la maestra, y se genera un backup local. Se congelan nombres, descripciones, categorías, precios y tamaños; las imágenes se referencian por su ID local y digest, y se comprueba su integridad antes de preparar un destino. Los precios son propios de cada selección y no cambian los perfumes de la biblioteca.

Entrá a **Selecciones guardadas** para abrirlas después de reiniciar, sin conexión ni túnel al VPS. Cada ficha muestra los precios, los ml, las fotos y si están presentes ambas descripciones. Con **Retomar selección / editar precios** podés ajustar una carga y **guardar una nueva versión**, conservando la anterior. También queda guardada la lista original del cliente cuando la selección viene de esa búsqueda. No se almacenan credenciales de destino en la selección.

El cliente de destino puede estar en otro VPS: la selección es local e independiente del túnel. Primero se guarda; luego se configura el destino específico para comprobar coincidencias y categorías. **El envío real continúa bloqueado y todavía debe completarse y probarse para usar estos precios y tamaños en los INSERT. Guardar una selección no equivale a importarla.**

Los perfumes y las fotos se guardan en `instance/biblioteca.db` (SQLite). Esta base permanece en tu computadora al cerrar la app. En esta etapa está prohibido usar PostgreSQL como biblioteca: el inicio aborta antes de abrir conexiones o crear tablas si MASTER_DATABASE_URL no es SQLite. Así una URL de cliente puesta por error no recibe tablas del gestor.

## Configurar orígenes reales

### Silvina: conexión asistida

En una terminal de tu computadora dejá abierto el túnel:

```bash
ssh -p 5285 -N -o ExitOnForwardFailure=yes -L 127.0.0.1:55432:localhost:5432 root@200.58.126.102
```

En otra terminal local ejecutá:

```bash
cd /home/nico/Escritorio/PERFUMES/gestor-perfumes
.venv/bin/python conectar_silvina.py
```

El asistente lee exclusivamente la URL del `.env` de Silvina por SSH, sin imprimirla. Guarda la contraseña en `instance/silvina.url` con permisos 600 y el origen en `instance/connections.json`. Son archivos locales privados, excluidos de Git; no los compartas. No modifica archivos del VPS. Comprueba la conexión y el esquema con consultas de solo lectura; no copia productos hasta que los selecciones en el panel. No registra otras bases. Si la conexión falla, podés repetirlo; no se muestran tracebacks con credenciales.

Cuando termine, entrá en http://127.0.0.1:5055/sources y elegí `catalogo_silvina`. El panel actualizado lee el registro automáticamente. La terminal del túnel debe permanecer abierta mientras consultás productos. El puerto 8012 corresponde a la web y no se utiliza para este acceso a PostgreSQL.

### Configuración manual para otros orígenes

Para Juli, en el mismo VPS y con el mismo túnel, ejecutá `.venv/bin/python conectar_juli.py`. Lee únicamente `/var/www/catalogo/catalogo-juli/backend/.env`, valida la base confirmada `catalogo_juli` y registra el origen en modo lectura. No supone el usuario PostgreSQL: utiliza el indicado en ese archivo. Al terminar elegí `catalogo_juli` en el panel, consultá y presioná **Comparar con la maestra**. Lo ya guardado permanece intacto; solo los nuevos que confirmes se suman a la biblioteca local.

Gleidys está en el mismo VPS que Silvina. Con el mismo túnel abierto, ejecutá `.venv/bin/python conectar_gleidys.py`. El configurador lee exclusivamente `/var/www/catalogo/catalogo-gleidys/backend/.env` por SSH, valida que corresponda a `catalogo_gleidys` y agrega un origen de solo lectura sin reemplazar el de Silvina ni tocar la biblioteca. Si el nombre de base o la ruta difieren, aborta para revisar esa configuración. Después elegí `catalogo_gleidys` en el panel y presioná **Analizar todos los resultados y separar duplicados**.

1. Copiá `connections.example.json` como `connections.json` y agregá una entrada por cliente con rol `source`. El archivo no lleva contraseñas.
2. Configurá las URLs mediante variables de entorno locales. Preferí credenciales existentes con permisos SELECT exclusivamente. No pegues contraseñas en el chat.
3. Si necesitás un túnel al VPS, abrilo en otra terminal con `ssh -N -L 55432:127.0.0.1:5432 usuario@tu-vps`. Ajustá IP, usuario y puertos a tu instalación.
4. En la terminal del gestor, antes de iniciarlo:

```bash
export PERFUME_CONNECTIONS='/ruta/absoluta/connections.json'
export ORIGEN_DATABASE_URL='postgresql+psycopg://lector:clave@127.0.0.1:55432/catalogo_origen'
.venv/bin/python app.py
```

El nombre de base tiene que coincidir con `database` en el JSON. Cada origen debe usar su variable de conexión. Las claves con caracteres especiales requieren codificación para URL. No cambies permisos, usuarios ni estructura de producción como parte de esta configuración; si no disponés de credenciales adecuadas, revisamos otra forma de acceder a una copia de los datos.

## Construir la biblioteca, un origen por vez

**Filtro de productos activo:** las nuevas consultas y recuperaciones solo admiten categorías cuyo nombre identifique árabes, diseñador o nicho, con sus variantes de género y diferencias de tildes/mayúsculas. Se consideran también las categorías adicionales del producto. Relojes, accesorios, ropa, vapeadores y otras categorías ajenas no se incluyen; un producto con categoría de relojes se excluye incluso si está asignado además a una categoría de perfumes. La recuperación por ID también valida este filtro antes de leer imágenes. Los nombres genéricos «Masculinos», «Femeninos», «Unisex» o «Perfumes» solos no se consideran autorización suficiente; si otro origen usa esos nombres para perfumes, deben revisarse y autorizarse específicamente mediante `perfume_category_names` en su configuración. El filtro se aplica a nuevas recuperaciones y no elimina ni cambia perfumes que ya están guardados en la biblioteca.

### Comparación conjunta de la maestra y otro origen

En **Recuperar desde una base**, elegí el origen, consultalo y presioná **Comparar con la maestra**. Se muestra una lista conjunta con todos los perfumes ya guardados y los resultados de origen de esa búsqueda. Los guardados aparecen en verde y se conservan. Los repetidos del origen se tachan en rojo y no se suman. Los nombres parecidos se agrupan con sus coincidencias en renglones consecutivos, incluso si los IDs son iguales entre distintas bases (no se usan como identidad compartida).

Para cada parecido elegí **Es el mismo: no sumar** o **Es distinto: sumar**. Las decisiones de no sumar se guardan localmente cuando confirmás la comparación; el botón **Revisar de nuevo** permite deshacerlas. Si el nombre o la marca cambian en el origen, la decisión anterior deja de aplicarse y se vuelve a comparar. Los pendientes no se agregan automáticamente. Los nuevos vienen seleccionados; podés desmarcarlos. Al continuar se revisan fotos y descripciones antes del guardado definitivo de los productos.

Arriba de cada página hay contadores: perfumes en maestra y en la página de biblioteca; resultados en el origen; en la comparación, maestra, origen, nuevos, repetidos, pendientes y renglones visibles. El total de renglones incluye ambas procedencias y no es un total de perfumes únicos. Abajo se muestra cuántos elegiste y cuántos quedarían en la maestra. Los filtros mantienen las coincidencias agrupadas para que no pierdas el contexto.

Para lotes grandes, después de consultar un origen presioná **Analizar todos los resultados y separar duplicados**. No hace falta marcar los 300 productos a mano: los nuevos se seleccionan juntos, los duplicados exactos se omiten y los parecidos se muestran aparte sin marcar. El análisis también compara productos del mismo lote. Se ignoran diferencias de tildes, mayúsculas, puntuación, tamaño explícito en ml y la marca repetida al comienzo del nombre. No se borran palabras que identifican variantes; los nombres parecidos no se fusionan automáticamente. La marca ausente o distinta con el mismo nombre requiere revisión.

El análisis inicial solo consulta nombres; las fotos y ambos textos se recuperan al continuar con los elegidos. En la última revisión presioná **Guardar seleccionados definitivamente en biblioteca local**. Antes y después del guardado se hacen snapshots consistentes de SQLite en `instance/library-backups/`, con permisos 600 y verificación de integridad. Los datos confirmados no dependen de la pestaña, del túnel ni del VPS y permanecen al reiniciar el gestor. Las vistas previas sin confirmar sí son temporales. Los backups están en el mismo equipo: una copia fuera de esa computadora protege también ante fallas del disco.

1. Entrá a **Recuperar desde una base** y elegí el origen.
2. Buscá y marcá exclusivamente los productos que vos cargaste.
3. Revisá nombres, descripciones, imágenes y posibles duplicados.
4. Confirmá **Agregar seleccionados a biblioteca**. Se escribe únicamente en SQLite local.
5. Repetí con los demás clientes. Los duplicados exactos normalizados por nombre y marca se omiten y se conserva su procedencia; los parecidos requieren tu revisión manual. No se fusionan variantes automáticamente.

La biblioteca permite buscar y pegar listas del cliente. Los precios delimitados con `$`, `ARS` o ` - ` se ignoran; se conservan nombres como 212 y 9 PM. Otros formatos requieren revisión. Se pueden revisar categorías por nombre y palabras clave (diseñador, nicho, árabes, masculino, femenino, unisex), independientemente de sus IDs. **Las vistas de destino son simulaciones y no permiten confirmar envíos.**

## Protecciones aplicadas

- Cualquier solicitud de conexión de escritura o ejecución de importación aborta antes de conectar o generar un backup.
- Las conexiones PostgreSQL se inician con `default_transaction_read_only=on` y cada transacción usa `SET TRANSACTION READ ONLY`.
- Un filtro previo al envío de SQL solo admite consultas SELECT/SHOW y los ajustes internos de solo lectura y tiempos de espera. Rechaza INSERT, UPDATE, DELETE, DDL, TRUNCATE y cambios a READ WRITE.
- PostgreSQL también rechaza los intentos de escritura mediante SELECT, como nextval sobre secuencias permanentes.
- El endpoint de confirmación de importación aborta siempre. No se puede levantar el bloqueo con variables de entorno ni editando el JSON.
- No se crean ni reparan tablas, secuencias ni usuarios en los clientes.

Esto protege las operaciones realizadas por este gestor. Usar un usuario SELECT exclusivamente agrega además protección a nivel de permisos del servidor. No es una garantía sobre acciones de otros programas o administradores.

## Fotos y límites

Los nombres de los perfumes son clickeables tanto en el origen como en la biblioteca y las revisiones. La ficha se abre en otra pestaña para conservar tus casillas marcadas. Reproduce la estructura de `ProductDetailNuevo.jsx`: galería con miniaturas y ampliación, nombre, marca, precio «Consultar», 100 ml, descripción principal arriba y `short_description` en la pestaña Descripción, con Ver más/Ver menos. Conserva el formato de texto permitido por el catálogo (negrita, cursiva, subrayado, párrafos, listas y saltos de línea); no ejecuta HTML activo. Es una vista previa del contenido: los colores y ajustes específicos del cliente pueden variar. Se informa cuando faltan textos o fotos. La ficha no realiza envíos ni guarda productos por abrirla.

Las fotos binarias se guardan en la biblioteca local. Las rutas `/public/uploads/...` requieren `image_root` apuntando a una copia o montaje local de la carpeta de imágenes del origen; se leen y convierten a binarios. Si la referencia principal apunta a un binario inexistente, se usan las demás fotos asociadas disponibles y se muestra un aviso. Las fotos vacías, demasiado grandes, externas o de rutas no resueltas se omiten con una advertencia, sin copiar referencias rotas ni bloquear el resto del lote. Si no se recupera ninguna foto, el perfume aparece sin marcar y se puede excluir para guardar los demás; solo se guarda sin imágenes si lo seleccionás explícitamente. Los avisos quedan guardados en la biblioteca y se muestran en la ficha. Las descripciones se conservan igualmente. No se sobrescriben archivos del origen.

Hasta 1000 resultados por búsqueda de origen, 500 seleccionados por lote y 20 MB por foto. Las vistas previas vencen a los 30 minutos o al reiniciar. El panel escucha solo en tu computadora; no está preparado para publicarse en Internet.

## Pruebas

Las pruebas de integración con PostgreSQL temporal verifican recuperación de perfumes y fotos a SQLite, rechazo de escrituras SQL, rechazo de cambios de secuencias, bloqueo del endpoint de envío y bloqueo de PostgreSQL como biblioteca. También comprueban que ALLOW_CLIENT_WRITES=1 no evita estas protecciones. No se utilizaron bases de clientes reales.

El archivo `INSPECCION.md` documenta el esquema del SaaS inspeccionado. El envío a clientes queda deshabilitado hasta una futura solicitud explícita y un cambio de código revisado.
