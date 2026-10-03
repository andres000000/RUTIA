from typing import Annotated

from pydantic import AfterValidator, EmailStr

# Correo de ENTRADA normalizado a minúsculas. EmailStr solo baja a minúsculas
# el dominio: sin esto, una cuenta creada como "Juan@colegio.edu.co" no podía
# iniciar sesión escribiendo "juan@..." (el celular suele poner la primera
# letra en mayúscula, o al revés). Las búsquedas por correo, además, comparan
# con `func.lower(User.email)` para que las cuentas ya guardadas con
# mayúsculas sigan entrando.
NormalizedEmail = Annotated[EmailStr, AfterValidator(lambda value: value.lower())]
