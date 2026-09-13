"""El contrato del bot vive copiado en dos repos. Esta prueba los ata.

`services/bot_contract.py` (Acten) y `acten_contract.py` (bot) tienen que
ser byte a byte iguales. La única comprobación era un script manual que
no corre en ningún sitio; si uno de los dos cambiaba, el otro seguía en
verde y la firma dejaba de cuadrar en producción.

Al cambiar el contrato: copiar el archivo a los dos repos y actualizar
esta huella en las DOS pruebas (aquí y en tests/test_contract_parity.py
del bot).
"""

import hashlib
from pathlib import Path

HUELLA = "54e22a6160e4303bd0d8451563e38ea21942c127c885f903628fb909a57f62eb"


def test_el_contrato_no_cambio_sin_avisar():
    actual = Path(__file__).resolve().parents[1] / "services" / "bot_contract.py"
    assert hashlib.sha256(actual.read_bytes()).hexdigest() == HUELLA, (
        "bot_contract.py cambió: copia el mismo archivo al bot y actualiza la "
        "huella en las dos pruebas de paridad."
    )
