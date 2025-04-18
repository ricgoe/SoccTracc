import numpy as np
import cv2 as cv
import os
import itertools as it
from sklearn.neighbors import KNeighborsClassifier
from scipy.cluster.hierarchy import linkage, fcluster
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression

cwd = os.getcwd()
player_paths = os.path.join(cwd, ".faafo", "full_players")
torso_paths = os.path.join(cwd, ".faafo", "torsos")
masked_torso_paths = os.path.join(cwd, ".faafo", "masked_torsos")


class ShirtClassifier:
    def __init__(self):
        self.name = "Shirt Classifier"  # Do not change the name of the module as otherwise recording replay would break!
        self.current_frame = 1
        self.currently_tracked_objs = []
        self.torsos_bgr = []
        self.torso_means = []
        self.current_torsos_in_frame = []
        self.clusterer = None
        self.classifier = None
        self.labels_pred = None
        self.team_a_color = None
        self.team_b_color = None

    def start(self, data):
        self.clusterer = KMeans(n_clusters=3)
        self.classifier = KNeighborsClassifier(n_neighbors=8, n_jobs=-1)

    def stop(self, data):
        # remove faafo images, will be removed later anyways after
        player_files = [
            os.path.join(player_paths, file) for file in os.listdir(player_paths)
        ]
        torso_files = [
            os.path.join(torso_paths, file) for file in os.listdir(torso_paths)
        ]
        masked_torsos = [
            os.path.join(masked_torso_paths, file) for file in os.listdir(masked_torso_paths)
        ]
        for file_path in player_files:
            os.remove(file_path)
        for file_path in torso_files:
            os.remove(file_path)
        for file_path in masked_torsos:
            os.remove(file_path)

    def step(self, data):
        # TODO: Implement processing of a current frame list
        # The task of the shirt classifier module is to identify the two teams based on their shirt color and to assign each player to one of the two teams

        # Note: You can access data["image"] and data["tracks"] to receive the current image as well as the current track list
        # You must return a dictionary with the given fields:
        #       "teamAColor":       A 3-tuple (B, G, R) containing the blue, green and red channel values (between 0 and 255) for team A
        #       "teamBColor":       A 3-tuple (B, G, R) containing the blue, green and red channel values (between 0 and 255) for team B
        #       "teamClasses"       A list with an integer class for each track according to the following mapping:
        #           0: Team not decided or not a player (e.g. ball, goal keeper, referee)
        #           1: Player belongs to team A
        #           2: Player belongs to team B

        # Get images of detected objetcs
        self.get_players_boxes(data)  # Internally updates self.currently_tracked_objs

        # Get top half (torsos) of detected objects
        current_torsos = []
        for index, player in enumerate(self.currently_tracked_objs):
            torso = self.torso(player=player, idx=index)
            if torso is not None:
                current_torsos.append(torso)
        self.torsos_bgr.extend(current_torsos)  

        # Reduce Image features by calculating mean color (BGR) of image
        for indx, torso in enumerate(self.torsos_bgr):
            masked_torso = self.green_masking(torso, indx)  # Mask green pixels (pitch background)
            mean_pxl = self.calc_mean_img_color(masked_torso)
            if self.current_frame < 9:  # Collect data for training
                self.torso_means.append(mean_pxl)  # leave as list to avoid flattening to 1D by np.append() (behaves differently for some reason)
            self.current_torsos_in_frame.append(mean_pxl)


        # Clear list of BGR torsos to avoid processing them twice in next step
        self.torsos_bgr = []

        if self.current_frame < 8:
            self.team_a_color = (255, 255, 255)  # Fake-Color until actual color is calculated
            self.team_b_color = (0, 0, 0)
            self.labels_pred = [1] * len(data["tracks"])  # Fake-Labels until actual labels labels are calculated

        if self.current_frame == 8:
            self.torso_means = np.array(self.torso_means)  # cast as np.array for being able to use list as index
            self.clusterer.fit_predict(self.torso_means)

            # Remap labels to 0, 1, 2 (0 = Rest, 1 = Team A, 2 = Team B)
            labels = self.clusterer.labels_
            labels_remapped = self.organize_classes(labels)  

            # Fit classifier, use clustering labels in this frame to avoid unnecessary calculations
            self.classifier.fit(X=self.torso_means, y=labels_remapped)
            self.labels_pred = self.classifier.predict(self.current_torsos_in_frame).tolist() # Labels for players in this frame were calculated in prev. step, however 
            
            # Last step: Determine Team color
            # Will be cached, not calcutaed every frame
            a_indices = np.where(labels_remapped == 1)[0]  # np.where() returns tuple, first value is needed
            self.team_a_color = self.avg_team_color(a_indices)

            b_indices = np.where(labels_remapped == 2)[0]
            self.team_b_color = self.avg_team_color(b_indices)

            # Reset torsos for next frame
            self.torso_means = [] 

        elif self.current_frame >= 9:
            # After gathering the data and training the classifier:
            # images get cut and prepared (see top of method), down here: only classification happens
            self.torso_means = []
            self.current_torsos_in_frame = np.array(self.current_torsos_in_frame)
            self.labels_pred = (self.classifier.predict(X=self.current_torsos_in_frame)).tolist()

        self.currently_tracked_objs = []
        self.current_torsos_in_frame = []
        self.current_frame += 1

        return {
            "teamAColor": self.team_a_color,
            "teamBColor": self.team_b_color,
            "teamClasses": self.labels_pred,
        }

    def get_players_boxes(self, data):
        """Extracts all players' bounding boxes from image in data, slices players from image into np.Array"""
        img = data["image"]
        player_boxes = data["tracks"] 

        for idx, player_box in enumerate(player_boxes):
            x, y, w, h = player_box

            half_width = 0.5 * w
            half_height = 0.5 * h
            top_left_corner = (int(y - half_height), int(x - half_width))
            bottom_right_corner = (int(y + half_height), int(x + half_width))
            player = img[
                top_left_corner[0] : bottom_right_corner[0],
                top_left_corner[1] : bottom_right_corner[1],
            ]
            height, width, _ = player.shape
            if height == 0 or width == 0:
                self.currently_tracked_objs.append(np.zeros((3,3,3), dtype=np.uint8))  # Will be ignored in calc_mean_img_color()
            # cv.imwrite(f'.faafo/full_players/player_{idx}.jpg', player)
            self.currently_tracked_objs.append(player)

        return self.currently_tracked_objs

    def torso(self, player: np.array, idx: int):
        rows = len(player)
        # top half of player only interesting -> the part where shirt is
        torso = player[:int(np.ceil(0.5 *rows)), :, :]  
        # top half of player only interesting -> the part where shirt is
        # cv.imwrite(f'.faafo/torsos/player_{idx}.jpg', torso)
        if torso.size == 0:
            return np.zeros((3,3,3), dtype=np.uint8) # Will be ignored in calc_mean_img_color()
        return torso
        
    
    def organize_classes(self, labels: np.array) -> np.array:
        """Reorganizes class labels into a desired format: 0 (Rest), 1 (Team A), 2 (Team B).
        This method processes an array of labels by analyzing their frequency and remapping 
        them based on specific rules. The remapping ensures that the label with the least 
        occurrences is assigned to the "Rest" class (0), while the other labels are assigned 
        to "Team A" (1) and "Team B" (2).
        - Computes a histogram of label occurrences.
        - Adjusts label values by adding 5 to avoid conflicts during remapping.
        - Determines the label with the least occurrences and assigns it to class 0.
        - Reassigns the remaining labels to classes 1 and 2 based on their original values.
        Args:
            labels (np.array): Array of labels to be reorganized.
        Returns:
            labels_remapped (np.array): Reorganized labels with values 0, 1, and 2.
        """
        label_hist = np.bincount(labels)

        # Add Index to label_hist
        index_array = np.arange(0, len(label_hist))

        # Add index_array to label hist
        hist_indexed = np.vstack((index_array, label_hist))


        # If bin 0 (label 0) has most occurances and other labels have equally many occurances
        if (
            np.max(hist_indexed[1, :]) == hist_indexed[1, 0]
            and hist_indexed[1, 1] == hist_indexed[1, 2]
        ):
            labels += 5  # Change current labels
            hist_indexed[0] += 5  # Change label names in hist

            # For there are no clear teams: label mith most appearances muts NOT be 0, further specification impossible
            # Label 1 (with most occurances) will be label 0 and vice versa
            labels[labels == 6] = 0
            labels[labels == 5] = 1
            labels[labels == 7] = 2

        # If label 1 hast least occurances -> make label 1 label 0 and vice versa
        elif np.min(hist_indexed[1, :]) == hist_indexed[1, 1]:
            labels += 5
            hist_indexed[0] += 5

            # We know: former bin 1 of (0,1,2) has least opccurances -> should be label 0, is currently 6 (due to 5-Addition)
            # Same process as before, seperated for chain of thought clarification / understandability
            labels[labels == 6] = 0
            labels[labels == 5] = 1
            labels[labels == 7] = 2
            hist_indexed[0] -= 5  # Change label names in hist

        # If label 2 has least occurances -> make label 2 label 0 and vice versa
        elif np.min(hist_indexed[1, :]) == hist_indexed[1, 2]:
            labels += 5
            hist_indexed[0] += 5

            # We know: former bin 2 of (0,1,2) has least opccurances -> should be label 0, is currently 7 (due to 5-Addition)
            labels[labels == 7] = 0
            labels[labels == 6] = 1
            labels[labels == 5] = 2
            hist_indexed[0] -= 5

        return labels

    def green_masking(self, bgr_img: np.array, idx: int) -> np.array:
        """Applies a green mask to the BGR input-image, blacking out all green pixels.
        Used to remove green noise (pitch-background) from images.
        Args:
            bgr_img (np.array): Input image in BGR format.
            idx (int): Index of the player for debugging purposes.
        Returns:
            np.array: Image with green pixels blacked out.
        """
        # Convert BGR to HSV color space
        hsv_img = cv.cvtColor(bgr_img, cv.COLOR_BGR2HSV)

        # Define the lower and upper bounds for the green color in HSV
        lower_green = np.array([30, 40, 40])
        upper_green = np.array([80, 255, 255])

        # Create a mask for green pixels
        mask = cv.inRange(hsv_img, lower_green, upper_green)

        # Invert the mask to keep non-green pixels
        mask_inv = cv.bitwise_not(mask)

        # Apply the mask to the original image
        green_cleansed_img = cv.bitwise_and(bgr_img, bgr_img, mask=mask_inv)
        
        # Safe masked image for debugging purposes
        #cv.imwrite(f'.faafo/masked_torsos/player_{idx}_masked.jpg', green_cleansed_img)

        return green_cleansed_img
    
    def avg_team_color(self, players_indices: np.array) -> tuple:
        """Calculates the average color of a team based on the given players' images.
        Args:
            players (np.array): Array of player images.
        Returns:
            tuple: Average color in BGR format.
        """
        team_colors = self.torso_means[players_indices]
        team_color = np.clip(
            a=(np.mean(team_colors, axis=0).astype(int)),
            a_min=0,
            a_max=255,
            )

        return tuple(team_color.tolist())
    
    def calc_mean_img_color(self, img: np.array) -> tuple:
        """Calculates the mean color of an image. Ignores completely black pixels -> are caused by green masking and should not be considered for color calculation.
        Args:
            img (np.array): Input image.
        Returns:
            tuple: Mean color in BGR format.
        """
        pixels = img.reshape(-1, 3) # flatten image to 2D array (rows = pixels, columns = BGR values)
        mask = np.all(pixels != [0, 0, 0], axis=1) # filter out black pixels
        filtered_pixels = pixels[mask]
        if filtered_pixels.size == 0:
            return [60, 250, 0]
        if np.all(filtered_pixels == 0):
            return [60, 250, 0]
        mean_color = np.mean(filtered_pixels, axis=0).astype(int)

        return mean_color.tolist()