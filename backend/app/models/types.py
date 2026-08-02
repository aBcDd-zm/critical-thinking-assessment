from sqlalchemy import BigInteger, Integer, SmallInteger, Text
from sqlalchemy.dialects import mysql

UINT_BIGINT = (
    BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(Integer, "sqlite")
)
UINT_INT = Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")
UINT_TINYINT = SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")
MEDIUM_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
