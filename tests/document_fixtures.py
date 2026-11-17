"""Documentos válidos mínimos y sintéticos, generados solo en memoria."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from pypdf import PdfWriter


def pdf_bytes(tag='synthetic', user_password=None):
    buffer=BytesIO();writer=PdfWriter();writer.add_blank_page(width=10,height=10)
    writer.add_metadata({'/Synthetic':tag})
    if user_password is not None:
        writer.encrypt(user_password,owner_password='synthetic-owner')
    writer.write(buffer)
    return buffer.getvalue()


def docx_bytes():
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    return buffer.getvalue()
