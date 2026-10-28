# Gestor de perfumes

Biblioteca maestra SQLite local. Los orígenes se consultan en modo solo lectura. Las importaciones requieren un destino configurado expresamente y confirmación desde el panel.

## Naim: conectar una vez

En una terminal local, mantener abierto:

```bash
ssh -i ~/.ssh/donweb_ed25519 -p 5867 -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:55433:127.0.0.1:5432 vape@66.97.46.94
```

En otra terminal:

```bash
cd /home/nico/Escritorio/PERFUMES/gestor-perfumes
.venv/bin/python conectar_naim.py
.venv/bin/python app.py
```

El configurador solicita la contraseña de PostgreSQL sin mostrarla, comprueba la base `catalogo_naim`, el esquema y los IDs masculino 1, femenino 2, unisex 3 con consultas de solo lectura. Guarda la URL local con permisos 600 en `instance/naim.url` y agrega el destino sin reemplazar otros clientes. No importa productos ni modifica el VPS. El túnel debe seguir abierto durante las consultas y el envío.

## Poblar desde el panel

1. Abrir http://127.0.0.1:5055, pegar la lista, elegir el cliente/destino y buscar coincidencias. La app consulta sus productos por el túnel: marca los ya cargados y los activa y permite actualizar su precio: campo vacío conserva el actual, un importe escrito (incluido cero) se revisa antes de confirmar. No los importa nuevamente. Los parecidos se muestran para revisar. Precio y mililitros permanecen visibles desde el principio; en productos ya cargados se habilita precio y se deshabilita mililitros. El buscador modal también indica los ya cargados.
2. Revisar precios y mililitros. Precio vacío se guarda en cero. Guardar la selección con el nombre del cliente.
3. En Selecciones guardadas, abrir la selección y presionar **Retomar selección / editar precios**. Si se cambian precios, guardar una nueva versión y retomar esa versión.
4. Elegir `catalogo_naim` y **Revisar categorías y preparar envío**.
5. Revisar las categorías sugeridas y sus IDs. Se toma el género de la descripción breve y la categoría queda seleccionada. Si no hay un género claro o una categoría única en destino, se elige manualmente. La categoría original de la maestra no reemplaza la descripción breve. La selección de perfumes árabes queda a cargo del operador; no se deduce la familia a partir del género.
6. Revisar el resumen, escribir `catalogo_naim` y confirmar **Hacer backup y confirmar envío**.

Se requieren descripción principal y al menos una foto por producto nuevo. Si falta contenido, la revisión muestra el aviso junto a **Quitar de la selección y cargar a mano**. Al marcarlo se permite continuar con los demás sin exigir categoría ni revisión de duplicados para ese producto. Si no se quita, sigue bloqueado su envío. Los excluidos se agregan al resumen manual por renglón, con nombre, texto original y motivo; quedan consultables en el resultado, historial y descarga .txt. También se puede guardar una lista enteramente manual sin escribir en el destino. Se conservan ambas descripciones, marca, fotos y tamaño. Los nuevos productos quedan activos, con stock 12 y precio minorista propio de la selección (cero si no se ingresó).

Antes de importar se revisan también nombres parecidos en el destino, contemplando tildes, marca en diferente posición, tamaños explícitos y diferencias menores. Por ejemplo, «Odyssey Mandarin Sky Elixir - Armaf» y «ARMAF MANDARÍN SKY ELIXIR» requieren revisión. Se muestra el nombre e ID encontrado y se exige elegir **Es el mismo: omitir** o **Es otro perfume / presentación: importar nuevo**. No se fusionan variantes automáticamente. Al confirmar se vuelve a consultar el destino; una coincidencia nueva que no fue revisada bloquea el lote. Es una comparación aproximada: diferencias grandes de nombres o marcas pueden requerir revisión manual del catálogo.

El envío hace un backup completo con `pg_dump` y valida el archivo con `pg_restore` antes de escribir. Se necesitan ambos programas compatibles con la versión del servidor. Los backups privados quedan en `instance/backups/`. Si falla el backup no se escribe nada.

Dentro de una transacción se vuelven a comprobar categorías y duplicados. Coincidencias de nombre normalizado y marca (o marca ausente) se omiten, también al repetir un envío. Se insertan productos e imágenes con IDs nuevos. En existentes se activa el producto y solo se actualiza el precio minorista ingresado explícitamente; el resumen muestra precio anterior y nuevo antes de confirmar. Stock, tamaños, textos, imágenes, usuarios, administradores, pedidos, configuraciones y categorías se conservan. Además, un filtro SQL del importador bloquea UPDATE generales, DELETE, TRUNCATE, cambios de estructura, upserts e inserciones fuera de productos e imágenes. Solo autoriza las sentencias específicas de precio/activación preparadas para los IDs confirmados, comprobando nombre y precio anterior. Si el producto o precio cambió entretanto, cancela el lote. Un error revierte el lote completo; las secuencias pueden tener saltos normales.

Si la lista solo contiene actualizaciones de precio o activaciones, se abre directamente el resumen de confirmación. Un existente inactivo con precio vacío se activa conservando su precio. Uno activo sin precio ingresado se conserva sin cambios. Para una lista mixta, los cambios se guardan junto con la selección y se envían en la misma transacción que los productos nuevos. Los cambios pertenecen al destino elegido y no se pueden aplicar a otro cliente. La actualización afecta `product.price` cuando lo ingresaste y pone `product.is_active=true`; no cambia precios mayoristas ni opciones de presentación.

## Resultado de cada envío

Al finalizar se abre un resultado con nombres de los productos importados, los que ya existían y los renglones de la lista original que quedaron fuera: **Cargar a mano** y **Pendiente de revisar**. Los existentes no se cuentan como pendientes manuales. El resultado se guarda localmente y puede abrirse desde **Resultados de importación**, incluso después de reiniciar y sin conexión al VPS, o descargarse como texto.

Para identificar lo que falta, la selección debe conservar la lista original del cliente. Si no hay lista original, se avisa que no se puede calcular lo que quedó fuera. El resultado corresponde a ese envío y no se actualiza con cargas manuales o envíos posteriores.

En el resultado final, **Ver en catálogo** abre un modal que consulta el producto actual del destino: fotos, ambas descripciones con su formato y emojis, precio, tamaño, stock y estado. Funciona también para informes anteriores y productos omitidos por existir. Requiere el túnel abierto. Es una vista previa basada en el detalle del catálogo; los colores y ajustes particulares del sitio pueden variar. **Ver historial de importaciones** permite volver a cualquier resultado guardado.

## Otros clientes

Cada destino se registra en `instance/connections.json` con `database`, `url_file` privado o `url_env`, `roles: ["target"]`, `import_products: true` y `category_ids`, un objeto con claves `masculino`, `femenino`, `unisex` y los IDs reales de ese cliente. Se validan los nombres en la base de destino. No se reutilizan los IDs de la maestra. Cada VPS necesita su conexión o túnel y un puerto local propio.

## Biblioteca y orígenes

La maestra y las selecciones se guardan en `instance/biblioteca.db`; los backups SQLite están en `instance/library-backups/`. No se admite PostgreSQL como biblioteca maestra. Guardar una selección no envía productos.

Los configuradores existentes `conectar_silvina.py`, `conectar_juli.py` y `conectar_gleidys.py` conservan su funcionamiento como orígenes de solo lectura mediante el túnel local 55432. El filtro de recuperación admite familias árabe, diseñador y nicho; categorías genéricas requieren autorización explícita en el origen. No modifica la maestra ya construida.

La app escucha solo en 127.0.0.1:5055. No publicar en Internet. Nunca subir `instance/`, URLs privadas ni contraseñas a Git.

## Pruebas

```bash
.venv/bin/python -m unittest discover -v
```

La integración real requiere PostgreSQL temporal exclusivo en 127.0.0.1:55465, usuario local nico, y `PERFUMES_TEST_PG=1`. Nunca utiliza bases de clientes. Verifica precios, tamaño, imágenes, estado activo, stock, duplicados, rollback, conservación de datos existentes y consultas de origen de solo lectura.
