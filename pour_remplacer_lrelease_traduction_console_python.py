import subprocess, os

plugin_dir = r"C:\Users\Aude H\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\line_chainer"
ts  = os.path.join(plugin_dir, "i18n", "line_chainer_fr.ts")
qm  = os.path.join(plugin_dir, "i18n", "line_chainer_fr.qm")
lrelease = r"C:\Outils\FreeCAD\bin\lrelease.exe"

result = subprocess.run([lrelease, ts, "-qm", qm], capture_output=True, text=True)
print(result.stdout)
print(result.stderr)
print("QM créé ✔" if os.path.exists(qm) else "Échec ✗")
)