// Offline, source-matched preview rendering. Does not create editable equations.
const fs = require('fs');
const path = require('path');

function options(argv) {
  const out = {};
  for (let i=0; i<argv.length; i+=2) {
    if (!argv[i].startsWith('--') || !argv[i+1]) throw Error('Use --key value arguments');
    if (out[argv[i].slice(2)] !== undefined) throw Error('Duplicate option '+argv[i]);
    out[argv[i].slice(2)] = argv[i+1];
  }
  return out;
}

async function main() {
  const args = options(process.argv.slice(2));
  const allowed = new Set(['manifest','output-dir','packages','dpi','browser','geometry-mode','font-size-pt','padding-pt']);
  for (const key of Object.keys(args)) if (!allowed.has(key)) throw Error('Unknown option --'+key);
  if (!args.manifest || !args['output-dir']) throw Error('--manifest and --output-dir are required');
  if (!args.packages || !path.isAbsolute(args.packages)) throw Error('--packages must be the resolved absolute workspace package directory');
  // NODE_PATH alone is insufficient: ancestor node_modules wins resolution.
  const playwrightEntry = require.resolve(path.join(args.packages,'playwright'));
  const {chromium} = require(playwrightEntry);
  const inputManifest = fs.readFileSync(args.manifest);
  const manifest = JSON.parse(inputManifest.toString('utf8'));
  const crypto = require('crypto');
  const mode = args['geometry-mode'] || 'preserve';
  if (!['preserve','natural'].includes(mode)) throw Error('geometry-mode must be preserve or natural');
  const fontSize = Number(args['font-size-pt'] || 11);
  const padding = Number(args['padding-pt'] || 0.6);
  if (!Number.isFinite(fontSize) || fontSize < 4 || fontSize > 72) throw Error('font-size-pt must be 4–72');
  if (!Number.isFinite(padding) || padding < 0 || padding > 4) throw Error('padding-pt must be 0–4');
  const beforeNatural = path.join(path.dirname(args.manifest),'formulas.before-natural.json');
  const completionFile = path.join(path.dirname(args.manifest),'render_completion.json');
  const validationFile = path.join(path.dirname(args.manifest),'preview_validation.json');
  const runtimeFile = path.join(path.dirname(args.manifest),'runtime_validation.json');
  if ([completionFile,validationFile,runtimeFile].some(file=>fs.existsSync(file))) throw Error('Render reports already exist; use a fresh work directory');
  if (mode === 'natural' && fs.existsSync(beforeNatural)) throw Error('Natural-mode input backup already exists; use a fresh work directory');
  const indices = manifest.formulas.map(f=>f.index);
  if (indices.some(i=>!Number.isInteger(i) || i<1) || new Set(indices).size !== indices.length) throw Error('Formula indices must be positive unique integers');
  const output = path.resolve(args['output-dir']);
  if (indices.some(i=>fs.existsSync(path.join(output,`equation${String(i).padStart(4,'0')}.png`)))) throw Error('Preview output already exists; use a fresh work directory');
  const dpi = Number(args.dpi || 600);
  if (!Number.isFinite(dpi) || dpi < 300 || dpi > 1200) throw Error('DPI must be 300–1200');
  const browserPath = args.browser ? path.resolve(args.browser) : chromium.executablePath();
  if (!fs.existsSync(browserPath)) throw Error('The resolved Chromium is not installed. Supply --browser with a verified installed local browser; do not switch packages or auto-download.');
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:browserPath});
  const runtime = {node_executable:process.execPath,playwright_entry:playwrightEntry,
                   browser_executable:browserPath,browser_version:browser.version(),
                   browser_explicitly_selected:!!args.browser,network:'blocked',geometry_mode:mode,
                   input_manifest_sha256:crypto.createHash('sha256').update(inputManifest).digest('hex')};
  const checks = [];
  try {
    const context = await browser.newContext({viewport:{width:3200,height:1400},deviceScaleFactor:dpi/96});
    await context.route('**/*', route => route.abort());
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('DOM.enable');
    await cdp.send('CSS.enable');
    for (const f of manifest.formulas) {
      const width = f.width_pt*96/72, height = f.height_pt*96/72;
      if (!(width>0 && height>0 && width<3000 && height<1300)) throw Error('Invalid box '+f.index);
      await page.setContent(`<!doctype html><html><head><meta charset="utf-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
      <style>body{margin:0;background:transparent;color:#000}
      #box{position:relative;width:${width}px;height:${height}px;overflow:visible}
      #formula{position:absolute;display:inline-block;white-space:nowrap;line-height:1;transform-origin:top left;font-size:${fontSize}pt}
      math{font-family:'Cambria Math','STIX Two Math','STIXGeneral',serif;font-size:${fontSize}pt;color:black}
      #baseline{display:inline-block;width:0;height:0;vertical-align:baseline}</style>
      </head><body><div id="box"><div id="formula">${f.mathml}<span id="baseline"></span></div></div></body></html>`);
      const geometry = await page.evaluate(async ({width,height,tag,mode,padding}) => {
        await document.fonts.ready;
        const math = document.querySelector('math');
        if (!math) throw Error('Missing MathML root');
        if (tag) {
          const tail = [...math.children].slice(-3);
          if (tail.length !== 3 || tail[0].textContent !== '(' || !/^\d+$/.test(tail[1].textContent) || tail[2].textContent !== ')') {
            throw Error('Confirmed tag does not match expected trailing structure');
          }
          const label = document.createElementNS('http://www.w3.org/1998/Math/MathML','mtext');
          label.textContent = '(' + tail[1].textContent + ')';
          tail.forEach(el => el.remove());
          math.appendChild(label);
        }
        const formula = document.getElementById('formula');
        const rect = (mode === 'natural' ? math : formula).getBoundingClientRect();
        const baseline = document.getElementById('baseline').getBoundingClientRect().top;
        if (!(rect.width>0 && rect.height>0)) throw Error('Zero-size math');
        const pad = padding*96/72;
        const scale = mode === 'natural' ? 1 : Math.min(width/rect.width,height/rect.height);
        const finalWidth = mode === 'natural' ? Math.ceil(rect.width+2*pad) : width;
        const finalHeight = mode === 'natural' ? Math.ceil(rect.height+2*pad) : height;
        if (!(finalWidth>0 && finalHeight>0 && finalWidth*72/96<=600 && finalHeight*72/96<=600)) {
          throw Error('Invalid/excessive rendered geometry; review the formula rather than silently scaling');
        }
        formula.style.transform = `scale(${scale})`;
        const left = mode === 'natural' ? pad-rect.left : (width-rect.width*scale)/2;
        const top = mode === 'natural' ? pad-rect.top : (height-rect.height*scale)/2;
        formula.style.left = `${left}px`;
        formula.style.top = `${top}px`;
        const box = document.getElementById('box');
        box.style.width = `${finalWidth}px`;
        box.style.height = `${finalHeight}px`;
        const baselineFromTop = mode === 'natural' ? baseline-rect.top+pad : top+(baseline-rect.top)*scale;
        return {width_pt:finalWidth*72/96,height_pt:finalHeight*72/96,
                natural_width_pt:rect.width*72/96,natural_height_pt:rect.height*72/96,scale,
                baseline_from_top_pt:baselineFromTop*72/96,descent_pt:(finalHeight-baselineFromTop)*72/96,
                review_required:scale<0.8 || scale>1.3,tag_normalized:tag};
      },{width,height,tag:!!f.confirmed_equation_tag,mode,padding});
      const dom = await cdp.send('DOM.getDocument');
      const leaf = await cdp.send('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'math mi,math mn,math mo,math mtext'});
      let fonts = [];
      if (leaf.nodeId) fonts = (await cdp.send('CSS.getPlatformFontsForNode',{nodeId:leaf.nodeId})).fonts.map(x=>x.familyName);
      const file = path.join(output,`equation${String(f.index).padStart(4,'0')}.png`);
      await page.locator('#box').screenshot({path:file,omitBackground:true});
      if (mode === 'natural') {
        f.width_pt = geometry.width_pt;
        f.height_pt = geometry.height_pt;
        f.geometry_change = {width_pt:f.width_pt,height_pt:f.height_pt};
      }
      checks.push({index:f.index,font_size_pt:fontSize,geometry_mode:mode,dpi,
                   png_sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),
                   mathml_sha256:crypto.createHash('sha256').update(f.mathml,'utf8').digest('hex'),
                   native_sha256:f.native_sha256,sampled_actual_fonts:fonts,...geometry});
    }
  } finally {
    await browser.close();
  }
  if (!fs.readFileSync(args.manifest).equals(inputManifest)) throw Error('Manifest changed while rendering; stop');
  if (mode === 'natural') {
    const temporary = args.manifest+'.natural.tmp';
    if (fs.existsSync(temporary)) throw Error('Temporary manifest already exists; stop');
    fs.writeFileSync(beforeNatural,inputManifest,{flag:'wx'});
    fs.writeFileSync(temporary,JSON.stringify(manifest,null,2),{flag:'wx'});
    fs.renameSync(temporary,args.manifest);
  }
  fs.writeFileSync(validationFile,JSON.stringify(checks,null,2),{flag:'wx'});
  fs.writeFileSync(runtimeFile,JSON.stringify(runtime,null,2),{flag:'wx'});
  const completion = {schema:1,png_directory:output,
    manifest_sha256:crypto.createHash('sha256').update(fs.readFileSync(args.manifest)).digest('hex'),previews:checks};
  fs.writeFileSync(completionFile,JSON.stringify(completion,null,2),{flag:'wx'});
  console.log(`Rendered ${checks.length} previews; ${checks.filter(c=>c.review_required).length} scale-review flags. Visual QA still required.`);
}
main().catch(error=>{process.stderr.write(String(error.stack || error)+'\n');process.exitCode=1;});
