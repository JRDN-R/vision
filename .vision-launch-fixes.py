from pathlib import Path
p=Path('vision-pc/server.py');s=p.read_text();s=s.replace(", anonymousTrial={'enabled': trials.enabled(), 'durationSeconds': 300}","");p.write_text(s)
p=Path('tests/launch-trial-smoke.cjs');s=p.read_text()
s=s.replace("await page.locator('#addNodeButton').click();assert.equal", "await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);assert.equal")
s=s.replace("await page.locator('#addNodeButton').click();await page.evaluate(()=>window.__trialTest.expire());", "await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);await page.evaluate(()=>window.__trialTest.expire());")
s=s.replace("backup:()=>projectBackup()", "backup:()=>projectBackup(),signOut:()=>accountGoogleSignOut()")
s=s.replace("  assert.deepEqual(errors,[]);await context.close();", """  // A Google sign-in during an active trial must discard temporary content too.
  await page.evaluate(async()=>{await window.__trialTest.signOut();localStorage.removeItem('vision-guest-receipt-v1');});
  await page.reload();await page.waitForFunction(()=>window.__trialTest);await page.evaluate(()=>window.__trialTest.ready());
  await page.locator('#accountGateTrial').click();await page.waitForFunction(()=>window.__trialTest.active());
  await page.locator('#addNodeButton').click();await page.waitForFunction(()=>window.__trialTest.state().nodes.length===1);
  await page.evaluate(()=>{document.getElementById('consoleKey').value='temporary-key';});
  await page.locator('#trialSignIn').click();await page.waitForFunction(()=>window.__trialTest.signed());
  assert.equal(await page.evaluate(()=>window.__trialTest.state().nodes.length),0);
  assert.equal(await page.locator('#consoleKey').inputValue(),'');
  assert.equal(await page.locator('#trialBanner').isVisible(),false);
  assert.deepEqual(errors,[]);await context.close();""")
p.write_text(s)
