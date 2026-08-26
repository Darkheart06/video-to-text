-- Установщик приложения «Расшифровка записей».
-- Всю работу делает install-payload.sh, апплет только показывает ход дела.

property appTitle : "Расшифровка записей"
property logHint : "Подробности — в файле ~/Library/Logs/Расшифровка-установка.log"

on run
	set myPath to POSIX path of (path to me)
	set sh to quoted form of (myPath & "Contents/Resources/payload/install-payload.sh")

	set intro to "Приложение расшифровывает аудио и видео прямо на этом компьютере: " & ¬
		"делает транскрипт с таймкодами, разделяет по спикерам и составляет саммари, " & ¬
		"бриф, задачи и решения." & return & return & ¬
		"Установка займёт 5–15 минут — нужно скачать около 1,5 ГБ. " & ¬
		"Нужен интернет; компьютер в это время можно не трогать."

	display dialog intro with title appTitle buttons {"Отмена", "Установить"} ¬
		default button "Установить" cancel button "Отмена" with icon note

	set stepList to {¬
		{"prepare", "Готовлю файлы приложения"}, ¬
		{"python", "Скачиваю Python"}, ¬
		{"deps", "Ставлю библиотеки — это самый долгий шаг"}, ¬
		{"ffmpeg", "Скачиваю ffmpeg"}, ¬
		{"models", "Скачиваю модели для разделения по спикерам"}, ¬
		{"bundle", "Собираю приложение"}, ¬
		{"verify", "Проверяю, что всё работает"}}

	set total to count of stepList
	set progress total steps to total
	set progress completed steps to 0
	set progress description to "Установка «Расшифровки»"
	set progress additional description to "Начинаю…"

	repeat with i from 1 to total
		set stepId to item 1 of (item i of stepList)
		set stepText to item 2 of (item i of stepList)
		set progress description to stepText
		set progress additional description to "Шаг " & i & " из " & total
		try
			with timeout of 7200 seconds
				do shell script sh & " " & stepId
			end timeout
		on error errText number errNum
			if errNum is -128 then error number -128
			set progress additional description to "Не получилось"
			display alert "Установка прервалась" message ¬
				stepText & " — не получилось." & return & return & errText & return & return & logHint ¬
				as critical
			return
		end try
		set progress completed steps to i
	end repeat

	set progress description to "Готово"
	set progress additional description to ""

	-- Что есть в системе после установки
	set appsFolder to "/Applications"
	set hasOllama to false
	try
		set stateText to do shell script sh & " state"
		repeat with ln in paragraphs of stateText
			set ln to ln as text
			if ln starts with "apps=" then set appsFolder to text 6 thru -1 of ln
			if ln is "ollama=yes" then set hasOllama to true
		end repeat
	end try
	set appPath to appsFolder & "/Расшифровка.app"

	set doneText to "Приложение установлено в «" & appsFolder & "»." & return & return & ¬
		"Перетащите в его окно запись — и получите транскрипт, саммари и бриф. " & ¬
		"Результаты складываются в папку «Расшифровка записей» в ваших документах."
	if not hasOllama then
		set doneText to doneText & return & return & ¬
			"Осталось одно: для саммари нужна языковая модель. Проще всего поставить Ollama — " & ¬
			"без неё приложение всё равно работает, но делает только транскрипт."
	end if

	if hasOllama then
		display dialog doneText with title appTitle buttons {"Закрыть", "Открыть приложение"} ¬
			default button "Открыть приложение" with icon note
		if button returned of result is "Открыть приложение" then
			do shell script "open " & quoted form of appPath
		end if
	else
		display dialog doneText with title appTitle ¬
			buttons {"Закрыть", "Открыть приложение", "Скачать Ollama"} ¬
			default button "Скачать Ollama" with icon note
		set choice to button returned of result
		if choice is "Скачать Ollama" then
			open location "https://ollama.com/download"
		else if choice is "Открыть приложение" then
			do shell script "open " & quoted form of appPath
		end if
	end if
end run
