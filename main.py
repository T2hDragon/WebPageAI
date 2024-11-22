import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
import openai
from dotenv import load_dotenv
from src.crawler.crawler import crawl_site

load_dotenv()

client = openai.OpenAI()
WEBSITE = os.getenv("BASE_URL")
MAX_TEXT_LENGTH = 196_000

@asynccontextmanager
async def lifespan(app: FastAPI):
	print(f"Crawling site '{WEBSITE}'")
	app.state.site_data = await crawl_site(WEBSITE)
	yield

app = FastAPI(lifespan=lifespan)

@app.get("/source_info")
async def get_source():
	return app.state.site_data

@app.post("/ask")
async def ask_question(request: Request):
	question = (await request.body()).decode('utf-8')
	if len(question) > 500:
		raise HTTPException(status_code=400, detail="Question exceeds maximum length of 500 characters")

	site_data_for_open_ai = await _enforce_site_data_limit(app.state.site_data)

	response = client.beta.chat.completions.parse(
		model="gpt-4o-mini",
		messages=[
		{"role": "system", "content": (
			"You are an AI assistant trained to answer questions based on the information provided "
			"from the crawled website. You should answer as accurately as possible based solely on "
			"the content of the website data, without including any outside information. If the question "
			"cannot be answered based on the provided data, indicate that clearly."
		)},
		{"role": "user", "content": (
			f"I have the following site data available, which is about the website '{WEBSITE}'. "
			"Please read through the data and answer the following question: \n\n"
			f"Website data:\n\n{site_data_for_open_ai}\n\nQuestion: {question}\n"
			"Provide a clear and concise answer based on the information above."
		)}
	],
	)

	answer = response.choices[0].message
	if answer.refusal:
		raise HTTPException(status_code=400, detail="The AI refused to answer the question based on the provided data.")

	usage = response.usage.to_dict()
	return {
		"response": {
			"user_question": question,
			"answer": answer.content,
			"usage": {
				"input_tokens": usage.get("prompt_tokens"),
				"output_tokens": usage.get("completion_tokens")
			},
			"sources": list((site_data_for_open_ai).keys())
		}
	}

@app.get("/openapi.json")
def get_openapi_endpoint():
	return JSONResponse(content=get_openapi(
		title="WepPage AI API",
		version="1.0.0",
		routes=app.routes,
	))

async def _enforce_site_data_limit(site_data: dict[str, str]) -> dict[str, str]:
	site_data_with_data_limit = site_data.copy()
	total_text_length = len(str(site_data_with_data_limit))
	if total_text_length > MAX_TEXT_LENGTH:
		print(f"Total text length {total_text_length} exceeds the limit of {MAX_TEXT_LENGTH}. Truncating data. Use less max_depth to avoid truncation.")
		# Reversing the order by the longest link first because longest link info could contain less broad information
		for url, text in sorted(site_data_with_data_limit.items(), key=lambda x: len(x[1]), reverse=True):
			if total_text_length <= MAX_TEXT_LENGTH:
				break
			del site_data_with_data_limit[url]
			total_text_length -= len(text)

	return site_data_with_data_limit
