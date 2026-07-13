import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from auto_mod_classifier.ui import main

    main()
