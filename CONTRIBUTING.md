# Contribuir

Gracias por ayudar a mejorar la integración. Para cambios funcionales, abre un issue o describe el caso de uso y la documentación oficial relevante en tu pull request.

1. Instala Rust estable con los componentes rustfmt y Clippy.
2. Implementa el cambio de forma acotada y documenta permisos, efectos y límites de la API.
3. Añade pruebas con datos y credenciales simuladas. No uses cuentas de producción para ejecutar pruebas automáticas.
4. Ejecuta `cargo fmt --all -- --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked --all-targets` y `cargo build --locked --release`.
5. Explica qué plataformas y flujos comprobaste y cuáles no.

No incluyas secretos, datos de clientes, logs privados ni información de cuentas en issues, commits o ejemplos. Para temas de seguridad consulta [SECURITY.md](SECURITY.md).
