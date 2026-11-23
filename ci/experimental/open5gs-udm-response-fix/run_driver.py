"""Run preserved subscriber-free cases against freshly verified candidate libs."""
import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'open5gs-udm-driver'))
import run_driver
out=pathlib.Path(sys.argv[1]);candidate=json.loads((out/'candidate.json').read_text())
run_driver.IMAGE=candidate['image_id']
sys.exit(run_driver.run(out))
