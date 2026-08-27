import fitz, os

base = os.path.dirname(os.path.abspath(__file__))
pdfs = [
    "ziran-ziyuan-tingsheng-guiding.pdf",
    "guowuyuan-gongbao-202524.pdf",
]
out = os.path.join(base, "rendered")
os.makedirs(out, exist_ok=True)

for name in pdfs:
    path = os.path.join(base, name)
    doc = fitz.open(path)
    n = min(3, doc.page_count)
    for i in range(n):
        page = doc[i]
        pix = page.get_pixmap(dpi=200)
        txt = page.get_text().strip()
        outname = f"{os.path.splitext(name)[0]}_p{i+1}.png"
        pix.save(os.path.join(out, outname))
        print(f"{outname}: {pix.width}x{pix.height}px | source_has_text_layer={'YES' if txt else 'NO'} (chars={len(txt)})")
    doc.close()
print("RENDER DONE")
