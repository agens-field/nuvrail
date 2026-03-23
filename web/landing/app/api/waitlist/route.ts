import { NextRequest, NextResponse } from 'next/server';

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);

  if (!body || !body.email) {
    return NextResponse.json({ error: 'Email is required.' }, { status: 400 });
  }

  const apiKey = process.env.LOOPS_API_KEY;
  if (!apiKey) {
    console.error('[waitlist] LOOPS_API_KEY is not set');
    return NextResponse.json(
      { error: 'Server configuration error.' },
      { status: 500 }
    );
  }

  const payload: Record<string, string> = {
    email: body.email,
    userGroup: 'waitlist',
    source: 'landing-page',
  };

  if (body.companyName) {
    payload.companyName = body.companyName;
  }

  if (body.deployingAgents) {
    payload.deployingAgents = body.deployingAgents;
  }

  let loopsResponse: Response;
  try {
    loopsResponse = await fetch('https://app.loops.so/api/v1/contacts/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('[waitlist] Loops fetch error:', err);
    return NextResponse.json(
      { error: 'Failed to reach mailing list service.' },
      { status: 502 }
    );
  }

  if (!loopsResponse.ok) {
    const text = await loopsResponse.text().catch(() => '');
    console.error('[waitlist] Loops error response:', loopsResponse.status, text);
    return NextResponse.json(
      { error: 'Mailing list service returned an error.' },
      { status: 502 }
    );
  }

  return NextResponse.json({ success: true }, { status: 200 });
}
