import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {startCaptureBridge} from '/app/core/meetings/services/bot/dist/capture-bridge.js';
const {chromium}=createRequire('/app/core/meetings/modules/remote-browser/package.json')('playwright');
process.env.VEXA_TEAMS_MAIN_AUDIO_GRACE_MS='1';
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
try {
  const context=await browser.newContext();
  await context.addInitScript({path:process.env.VEXA_BROWSER_UTILS_PATH});
  const page=await context.newPage();
  await page.goto('data:text/html,<html><body>synthetic-audio</body></html>');
  await page.evaluate(async()=>{
    const ctx=new AudioContext({sampleRate:16000}); await ctx.resume();
    const source=ctx.createOscillator(); source.frequency.value=440;
    const gain=ctx.createGain(); gain.gain.value=0.001;
    const output=ctx.createMediaStreamDestination(); source.connect(gain); gain.connect(output); source.start();
    const dead=output.stream.clone(); dead.getAudioTracks().forEach(track=>track.stop());
    Object.defineProperty(dead,'id',{value:'same-server-slot'});
    Object.defineProperty(output.stream,'id',{value:'same-server-slot'});
    window.__vexaCapturedRemoteAudioStreams=[dead,output.stream];
    window.__testAudioContext=ctx;
  });
  let frames=0, samples=0;
  const pipeline={start:async()=>{},stop:async()=>{},feedAudio(){},feedMixedAudio(pcm){frames++; if(pcm.some(value=>Math.abs(value)>0.00001))samples+=pcm.length;},recordHint(){},recordTransportEvent(){},recordRosterName(){},recordRosterCoverage(){}};
  const stop=await startCaptureBridge(page,{platform:'teams',transcriptionModel:'local',botName:'Synthetic capture test'},pipeline);
  const deadline=Date.now()+8000;
  while(samples<16000 && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(frames>0,'replacement live track must deliver PCM after the earlier same-slot track ended');
  assert.ok(samples>=16000,'at least one second of synthetic PCM must cross the bridge');
  const before=samples;
  await page.evaluate(async()=>{
    for(const stream of window.__vexaCapturedRemoteAudioStreams)stream.getAudioTracks().forEach(track=>track.stop());
    await window.__testAudioContext.close();
    const ctx=new AudioContext({sampleRate:16000});await ctx.resume();
    const source=ctx.createOscillator(),gain=ctx.createGain(),output=ctx.createMediaStreamDestination();
    gain.gain.value=0.001;source.connect(gain);gain.connect(output);source.start();
    Object.defineProperty(output.stream,'id',{value:'same-server-slot'});
    window.__vexaCapturedRemoteAudioStreams.push(output.stream);
    window.__testAudioContext=ctx;
  });
  const replacementDeadline=Date.now()+8000;
  while(samples<before+16000 && Date.now()<replacementDeadline)await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(samples>=before+16000,'audio must resume after replacing the live track in the same stream slot');
  const activeSources=await page.evaluate(()=>window.__vexaMixSources.size);
  assert.equal(activeSources,1,'ended sources must be disconnected rather than accumulated');
  await stop();
  console.log(`PASS: ended same-slot stream ignored; quiet live track delivered ${frames} PCM frames`);
} finally {await browser.close();}
