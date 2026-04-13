import os, json, groq

client = groq.Groq(api_key=os.getenv('GROQ_API_KEY'))

prompt = """You are a startup validation agent. Use your tools to research this idea if needed. Ensure you evaluate the logic of the idea relative to the heuristic scores provided. 
CRITICAL SANITY CHECK: If the idea is a parody, intrinsically absurd, illegal, or physically impossible (e.g., physical email delivery bikes, generating gravity with hamsters), you MUST overrule the Initial Validation Score! Set `sanity_check` to false and lower the `adjusted_score` strictly to between 0 and 15! If it is a genuine idea, set `sanity_check` to true and provide an `adjusted_score` that matches or slightly tweaks the Initial Validation Score.

Respond with ONLY a raw JSON object — no markdown, no code blocks.

Startup Idea: free wifi
Description: 
Industry: technology
Initial Validation Score: 58/100
Verdict: Promising
Market Trend: rising (score: 56/100)
Public Sentiment: negative (score: 41/100)
Competition Level: medium (score: 50/100)
Problem Urgency: 100/100
Number of Competitors: 3
Total Addressable Market: $5200B

Respond exactly with this JSON:
{
  "adjusted_score": 59,
  "sanity_check": true,
  "summary": "2-3 sentence executive summary specific to this exact idea and its market position",
  "strengths": ["very specific strength 1 about this idea", "very specific strength 2", "very specific strength 3"],
  "weaknesses": ["very specific weakness 1 about this idea", "very specific weakness 2"],
  "opportunities": ["very specific opportunity 1 for this idea", "very specific opportunity 2", "very specific opportunity 3"],
  "threats": ["very specific threat 1 for this idea", "very specific threat 2"],
  "recommendation": "2-3 sentence actionable recommendation with concrete next steps specific to this idea",
  "risk_level": "Low",
  "risk_reason": "One sentence explaining the single biggest risk for this specific idea"
}"""

try:
    resp = client.chat.completions.create(
        model='llama-3.3-70b-versatile',
        messages=[{"role": "user", "content": prompt}]
    )
    print("RAW OUTPUT:")
    print(repr(resp.choices[0].message.content))
except Exception as e:
    print('ERROR:', e)
