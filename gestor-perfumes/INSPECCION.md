> Estado actual: SOLO LECTURA en bases de clientes. El envío está bloqueado desde el código; las referencias al adaptador de destino describen el diseño inspeccionado, no una operación habilitada. La biblioteca activa es SQLite local.

# Compatibilidad revisada

Repositorio: https://github.com/nicolasdelfino-123/catalogo-plantilla-base

Commit inspeccionado: `03daeab2a0c719690f7bf50c2e53ccccd3839064`.

- `backend/app/models.py`: Product, Category y ProductImage coinciden con los modelos compartidos. Product requiere nombre, precio, stock, categoría, estado, fecha y flavor_stock_mode. Category.name es único. ProductImage contiene binarios y una FK al producto. No existe columna de género ni jerarquía de categorías; el género puede estar expresado en sus nombres.
- `backend/app/routes/admin_bp.py`: `_normalize_volume_options` guarda ml, price, price_wholesale, price_strikethrough y stock; los precios no positivos se normalizan a NULL en las opciones. La carga de fotos guarda binarios en ProductImage. Las categorías múltiples se guardan como un objeto `multi_category_meta` en flavor_catalog con extra_category_ids.
- `backend/app/routes/public_bp.py`: `/public/img/<id>` sirve el binario; `/public/uploads/<filename>` sirve archivos de UPLOAD_FOLDER.
- `backend/app/config.py`: UPLOAD_FOLDER apunta a backend/uploads; la conexión real llega mediante SQLALCHEMY_DATABASE_URI. El gestor no importa ni inicia el SaaS.
- `frontend/src/components/ui/cards/ProductCardPerfumes.jsx`: contempla «Consultar» cuando no hay precio positivo.
- `backend/migrations/`: existen migraciones de las tablas y volume_options. El esquema efectivo puede variar por cliente; el gestor refleja y valida las tres tablas al preparar cada operación, sin aplicar migraciones.

Los modelos no permiten conocer el estado real de las secuencias de producción. Se validan sus defaults/identities y se utiliza pg_get_serial_sequence / nextval para generar referencias nuevas. Nunca se ejecuta setval, ALTER ni CREATE sobre los clientes. La asignación de categorías se hace por nombres revisados en el panel, y los IDs se buscan de nuevo dentro de la transacción de destino.

La recuperación utiliza transacciones de solo lectura y selecciona únicamente campos de producto, categorías y fotos. El destino usa INSERT; no modifica usuarios, carritos, pedidos ni direcciones. No se abrió ninguna conexión a bases de producción durante el desarrollo.
