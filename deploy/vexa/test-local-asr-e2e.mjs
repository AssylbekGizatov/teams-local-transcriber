// Opt-in smoke with a supplied public WAV; no browser or meeting connection.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createBotPipeline,createTranscribe} from '/app/core/meetings/services/bot/dist/pipeline.js';
const wav=await readFile(process.argv[2]);
assert.equal(wav.toString('ascii',0,4),'RIFF');
assert.equal(wav.toString('ascii',8,12),'WAVE');
let format=null, raw=null;
for(let offset=12;offset+8<=wav.length;){
  const kind=wav.toString('ascii',offset,offset+4),size=wav.readUInt32LE(offset+4),begin=offset+8;
  assert.ok(begin+size<=wav.length,'truncated WAV');
  if(kind==='fmt ')format={codec:wav.readUInt16LE(begin),channels:wav.readUInt16LE(begin+2),rate:wav.readUInt32LE(begin+4),bits:wav.readUInt16LE(begin+14)};
  if(kind==='data')raw=wav.subarray(begin,begin+size);
  offset=begin+size+(size%2);
}
assert.deepEqual(format,{codec:1,channels:1,rate:16000,bits:16});
assert.ok(raw?.length>0 && raw.length<=20*16000*2,'use a PCM WAV no longer than 20 seconds');
assert.ok(process.env.ASR_API_TOKEN,'ASR_API_TOKEN must be supplied locally');
const pcm=new Float32Array(raw.length/2);
for(let i=0;i<pcm.length;i++)pcm[i]=raw.readInt16LE(i*2)/32768;
const invocation={platform:'teams',botName:'Synthetic transcription test',transcribeEnabled:true,transcriptionModel:'local',transcriptionServiceUrl:'http://asr-app:8000',transcriptionServiceToken:process.env.ASR_API_TOKEN};
const transcribe=createTranscribe(invocation);
const reference=await transcribe(pcm);
assert.ok(reference.text.trim(),'real local ASR must recognize the supplied speech');
const segments=[],faults=[];
const pipeline=createBotPipeline(invocation,{publish:async s=>segments.push(s)},{onError:error=>faults.push(error)});
await pipeline.start();
const started=Date.now(),epoch=Date.now();
for(let offset=0;offset<pcm.length;offset+=4096)pipeline.feedMixedAudio(pcm.subarray(offset,Math.min(offset+4096,pcm.length)),epoch+offset/16);
await pipeline.stop();
assert.equal(faults.length,0,'local streaming path must not produce faults');
assert.equal(segments.length,1);
assert.equal(segments[0].text,reference.text.trim(),'the bot path must match direct ASR on identical PCM');
console.log(JSON.stringify({status:'PASS',audioSeconds:pcm.length/16000,streamSeconds:(Date.now()-started)/1000,textMatchesDirectAsr:true,segments:segments.length}));
