import asyncio
import sys
sys.path.append('.')
from src.services.llm import extract_jd_skills_with_llm

async def test_llm():
    skills, error = await extract_jd_skills_with_llm("We need a react developer who knows NextJS and tailwind.")
    print(f"Skills: {skills}")
    print(f"Error: {error}")

if __name__ == "__main__":
    asyncio.run(test_llm())
